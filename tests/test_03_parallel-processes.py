"""Try to upload and download as fast as possible using mutliple processes.
"""

from __future__ import print_function
import sys
import re
import threading
if sys.version_info < (3, 0):
    import Queue as queue
else:
    import queue
from subprocess import Popen, PIPE
import logging
from timeit import default_timer as timer
import yaml
import pytest
import icat
import icat.config
from icat.query import Query
from conftest import getConfig, wipe_data, script_cmdline
from conftest import Unbuffered, StatItem, Time, MemorySpace


log = logging.getLogger("test.%s" % __name__)

# ============================ testdata ============================

testInvestigation = "12100409-ST"
testDatasetName = "test_parallel-processes"
testDatasetCount = 200

# ============================= helper =============================

@pytest.fixture(scope="module")
def icatconfig(setupicat, testConfig, request):
    conf = getConfig(ids="mandatory")
    def cleanup():
        client = icat.Client(conf.url, **conf.client_kwargs)
        client.login(conf.auth, conf.credentials)
        query = Query(client, "Dataset", conditions={
            "name": "LIKE '%s-%%'" % testDatasetName
        })
        wipe_data(client, query)
        query.setLimit( (0,500) )
        while True:
            objs = client.search(query)
            if not objs:
                break
            client.deleteMany(objs)
    if testConfig.cleanup:
        request.addfinalizer(cleanup)
    return conf

# ============================= tests ==============================

@pytest.mark.parametrize("source", ["zero", "urandom", "file"])
@pytest.mark.parametrize("numProcs", [1, 2, 3, 4, 6, 8, 10, 12, 16, 20])
def test_upload(icatconfig, stat, tmpdir, source, numProcs):

    dsQueue = queue.Queue()
    resultQueue = queue.Queue()
    statusre = re.compile(r"^(READY|OK|ERROR|DONE)(?::\s*(.*))?$")
    
    def getStatus(proc):
        status = proc.stdout.readline().strip()
        m = statusre.match(status)
        if m:
            if m.group(1) == 'ERROR':
                raise RuntimeError("Error from worker: %s" % m.group(2))
            else:
                return m.groups()
        else:
            raise RuntimeError("Invalid status '%s'" % status)

    def uploadWorker(conf, source):
        args = conf.cmdargs + ["--source", source, testInvestigation]
        cmd = script_cmdline("upload-helper.py", args)
        helper = Popen(cmd, bufsize=0, stdin=PIPE, stdout=PIPE, 
                       universal_newlines=True)
        stcode, stmsg = getStatus(helper)
        resultQueue.put(stcode)
        while True:
            name = dsQueue.get()
            if name is None:
                print("", file=helper.stdin)
                break
            try:
                print(name, file=helper.stdin)
                stcode, stmsg = getStatus(helper)
                assert stcode == 'OK'
                stats = yaml.load(stmsg)
                statitem = StatItem("upload", **stats)
                resultQueue.put(statitem)
                log.info("Uploaded %s to dataset %s in %s (%s/s)", 
                         MemorySpace(statitem.size), name, Time(statitem.time), 
                         MemorySpace(statitem.size/statitem.time))
            except Exception as err:
                resultQueue.put(err)
            dsQueue.task_done()

    log.info("test_parallel-processes: source = '%s', numProcs = %d", 
             source, numProcs)
    threads = []
    for i in range(numProcs):
        t = threading.Thread(target=uploadWorker, args=(icatconfig, source))
        t.start()
        threads.append(t)
    # Wait for all worker to be ready.
    for i in range(numProcs):
        status = resultQueue.get()
        assert status == "READY"
    log.info("test_parallel-processes: helper started and ready")
    stag = {"zero":"z", "urandom":"r", "file":"f"}
    tag = "%s%02d" % (stag[source], numProcs)
    log.info("test_parallel-processes: start uploads")
    start = timer()
    for i in range(1, testDatasetCount+1):
        name = "%s-%s-%05d" % (testDatasetName, tag, i)
        dsQueue.put(name)
    dsQueue.join()
    for i in range(numProcs):
        dsQueue.put(None)
    for t in threads:
        t.join()
    end = timer()
    elapsed = end - start
    size = 0
    c = 0
    while True:
        try:
            r = resultQueue.get(block=False)
            c += 1
            if isinstance(r, StatItem):
                stat.add(r)
                size += r.size
            else:
                raise r
        except queue.Empty:
            break
    assert c == testDatasetCount
    log.info("test_parallel-processes: uploaded %s in %s (%s/s)", 
             MemorySpace(size), Time(elapsed), MemorySpace(size/elapsed))
    statitem = StatItem("upload", "test_parallel-processes-%s" % tag, 
                        int(size), float(elapsed))
    stat.add(statitem)
