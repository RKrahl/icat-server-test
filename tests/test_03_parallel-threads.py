"""Try to upload and download as fast as possible using mutliple threads.
"""

from __future__ import print_function
import sys
import os.path
import threading
if sys.version_info < (3, 0):
    import Queue as queue
else:
    import queue
import random
import tempfile
import logging
from timeit import default_timer as timer
import pytest
import icat
import icat.config
from icat.query import Query
from helper import Time, MemorySpace, StatItem, DatafileBase, DatasetBase
from conftest import getConfig, wipe_data, tmpdir


log = logging.getLogger("test.%s" % __name__)


# ============================ testdata ============================

testInvestigation = "12100409-ST"
testDatasetName = "test_parallel-threads"
testDatasetCount = 200

class PreparedRandomDatafile(DatafileBase):

    rndfile = None

    @classmethod
    def prepareRandomFile(cls, size, tmpdir):
        cls.rndfile = os.path.join(tmpdir.dir, "rndfile")
        chunksize = 8192
        with open("/dev/urandom", "rb") as infile:
            with open(cls.rndfile, "wb") as outfile:
                while size > 0:
                    if chunksize > size:
                        chunksize = size
                    chunk = infile.read(chunksize)
                    outfile.write(chunk)
                    size -= len(chunk)

    def __init__(self, size):
        super(PreparedRandomDatafile, self).__init__(size)
        assert self.rndfile
        self.data = open(self.rndfile, 'rb')

    def close(self):
        self.data.close()

    def _read(self, n):
        return self.data.read(n)

class Dataset(DatasetBase):
    fileCount = 4
    fileSize = MemorySpace("20 MiB")

def createDatasets(client, tag, source, tmpdir):
    testDatasets = []
    query = Query(client, "Investigation", conditions={
        "name": "= '%s'" % testInvestigation,
    })
    inv = client.assertedSearch(query)[0]
    if source == "file":
        PreparedRandomDatafile.prepareRandomFile(Dataset.fileSize, tmpdir)
        data = PreparedRandomDatafile
    else:
        data = source
    for i in range(1, testDatasetCount+1):
        name = "%s-%s-%05d" % (testDatasetName, tag, i)
        testDatasets.append(Dataset(client, inv, name, data=data))
    return testDatasets

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
@pytest.mark.parametrize("numThreads", [1, 2, 3, 4, 6, 8, 10, 12, 16, 20])
def test_upload(icatconfig, stat, tmpdir, source, numThreads):

    dsQueue = queue.Queue()
    resultQueue = queue.Queue()

    def uploadWorker(conf):
        client = icat.Client(conf.url, **conf.client_kwargs)
        client.login(conf.auth, conf.credentials)
        while True:
            dataset = dsQueue.get()
            if dataset is None:
                break
            try:
                statitem = dataset.uploadFiles(client)
                resultQueue.put(statitem)
            except Exception as err:
                resultQueue.put(err)
            dsQueue.task_done()
        client.logout()

    log.info("test_parallel-threads: source = '%s', numThreads = %d", 
             source, numThreads)
    threads = []
    for i in range(numThreads):
        t = threading.Thread(target=uploadWorker, args=(icatconfig,))
        t.start()
        threads.append(t)
    log.info("test_parallel-threads: create datasets")
    stag = {"zero":"z", "urandom":"r", "file":"f"}
    tag = "%s%02d" % (stag[source], numThreads)
    client = icat.Client(icatconfig.url, **icatconfig.client_kwargs)
    client.login(icatconfig.auth, icatconfig.credentials)
    testDatasets = createDatasets(client, tag, source, tmpdir)
    client.logout()
    size = len(testDatasets) * Dataset.getSize()
    log.info("test_parallel-threads: start uploads")
    start = timer()
    for dataset in testDatasets:
        dsQueue.put(dataset)
    dsQueue.join()
    for i in range(numThreads):
        dsQueue.put(None)
    for t in threads:
        t.join()
    end = timer()
    elapsed = Time(end - start)
    log.info("test_parallel-threads: uploaded %s in %s (%s/s)", 
             size, elapsed, MemorySpace(size/elapsed))
    c = 0
    while True:
        try:
            r = resultQueue.get(block=False)
            c += 1
            if isinstance(r, StatItem):
                stat.add(r)
            else:
                raise r
        except queue.Empty:
            break
    assert c == len(testDatasets)
    statitem = StatItem("upload", "test_parallel-threads-%s" % tag, 
                        int(size), float(elapsed))
    stat.add(statitem)
