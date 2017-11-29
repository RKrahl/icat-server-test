"""Try to upload and download as fast as possible using mutliple processes.
"""

from __future__ import print_function
import sys
import os.path
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
from helper import Time, MemorySpace, StatItem
from conftest import getConfig, wipe_data, script_cmdline, logfilename


log = logging.getLogger("test.%s" % __name__)

# ============================ testdata ============================

testInvestigation = "12100409-ST"
testDatasetName = "test_parallel-processes"
testDatasetCount = 200
testFileCount = 4
testFileSize = MemorySpace("20 MiB")

# ============================= helper =============================

@pytest.fixture(scope="module")
def icatconfig(setupicat, testConfig, request):
    client, conf, _ = getConfig(ids="mandatory")
    def cleanup():
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
        client.logout()
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

    def uploadWorker(conf, source, logfile):
        try:
            args = conf.cmdargs + ["--logfile=%s" % logfile, 
                                   "--fileCount=%d" % testFileCount, 
                                   "--fileSize=%s" % testFileSize, 
                                   "--source=%s" % source, 
                                   testInvestigation]
            cmd = script_cmdline("upload-helper.py", args)
            helper = Popen(cmd, bufsize=0, stdin=PIPE, stdout=PIPE, 
                           universal_newlines=True)
            stcode, stmsg = getStatus(helper)
            resultQueue.put(stcode)
        except Exception as err:
            resultQueue.put(err)
        while True:
            name = dsQueue.get()
            if name is None:
                break
            try:
                print(name, file=helper.stdin)
                stcode, stmsg = getStatus(helper)
                assert stcode == 'OK', "unexpected status code"
                stats = yaml.load(stmsg)
                statitem = StatItem("upload", **stats)
                resultQueue.put(statitem)
                log.info("Uploaded %s to dataset %s in %s (%s/s)", 
                         MemorySpace(statitem.size), name, Time(statitem.time), 
                         MemorySpace(statitem.size/statitem.time))
            except Exception as err:
                resultQueue.put(err)
            dsQueue.task_done()
        try:
            print("", file=helper.stdin)
            stcode, stmsg = getStatus(helper)
            retcode = helper.wait()
            assert retcode == 0, "unexpected return code"
            resultQueue.put(stcode)
        except Exception as err:
            resultQueue.put(err)

    stag = {"zero":"z", "urandom":"r", "file":"f"}
    tag = "%s%02d" % (stag[source], numProcs)
    logfile = "%s-%s-%%(pid)d.log" % (os.path.splitext(logfilename)[0], tag)
    log.info("test_parallel-processes: source = '%s', numProcs = %d", 
             source, numProcs)
    threads = []
    for i in range(numProcs):
        t = threading.Thread(target=uploadWorker, 
                             args=(icatconfig, source, logfile))
        t.start()
        threads.append(t)
    # Wait for all worker to be ready.
    for i in range(numProcs):
        status = resultQueue.get()
        if isinstance(status, str):
            assert status == "READY"
        else:
            raise status
    log.info("test_parallel-processes: start uploads")
    start = timer()
    for i in range(1, testDatasetCount+1):
        name = "%s-%s-%05d" % (testDatasetName, tag, i)
        dsQueue.put(name)
    dsQueue.join()
    end = timer()
    elapsed = end - start
    for i in range(numProcs):
        dsQueue.put(None)
    for t in threads:
        t.join()
    size = 0
    c = 0
    tc = 0
    while True:
        try:
            r = resultQueue.get(block=False)
            if isinstance(r, StatItem):
                c += 1
                stat.add(r)
                size += r.size
            elif isinstance(r, str):
                assert r == "DONE"
                tc += 1
            else:
                raise r
        except queue.Empty:
            break
    assert c == testDatasetCount
    assert tc == numProcs
    log.info("test_parallel-processes: uploaded %s in %s (%s/s)", 
             MemorySpace(size), Time(elapsed), MemorySpace(size/elapsed))
    statitem = StatItem("upload", "test_parallel-processes-%s" % tag, 
                        int(size), float(elapsed))
    stat.add(statitem)
