"""Try to upload and download as fast as possible using mutliple threads.
"""

from __future__ import print_function
import sys
import threading
if sys.version_info < (3, 0):
    import Queue as queue
else:
    import queue
import random
import logging
from timeit import default_timer as timer
import pytest
import icat
import icat.config
from icat.query import Query
from conftest import getConfig, wipe_data
from conftest import StatItem, DatasetBase, Time, MemorySpace


log = logging.getLogger("test.%s" % __name__)


# ============================ testdata ============================

testInvestigation = "12100409-ST"
testDatasetName = "test_parallel"
testDatasetCount = 200

class Dataset(DatasetBase):
    fileCount = 4
    fileSize = MemorySpace("20 MiB")

def createDatasets(client, tag):
    testDatasets = []
    query = Query(client, "Investigation", conditions={
        "name": "= '%s'" % testInvestigation,
    })
    inv = client.assertedSearch(query)[0]
    for i in range(1, testDatasetCount+1):
        name = "%s-%s-%05d" % (testDatasetName, tag, i)
        testDatasets.append(Dataset(client, inv, name))
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

@pytest.mark.parametrize("source", ["zero", "urandom"])
@pytest.mark.parametrize("numThreads", [1, 2, 3, 4, 6, 8, 10, 12, 16, 20])
def test_upload(icatconfig, stat, source, numThreads):

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

    log.info("test_parallel: source = '%s', numThreads = %d", 
             source, numThreads)
    threads = []
    for i in range(numThreads):
        t = threading.Thread(target=uploadWorker, args=(icatconfig,))
        t.start()
        threads.append(t)
    log.info("test_parallel: create datasets")
    stag = {"zero":"z", "urandom":"r"}
    tag = "%s%02d" % (stag[source], numThreads)
    client = icat.Client(icatconfig.url, **icatconfig.client_kwargs)
    client.login(icatconfig.auth, icatconfig.credentials)
    testDatasets = createDatasets(client, tag)
    client.logout()
    size = len(testDatasets) * Dataset.getSize()
    log.info("test_parallel: start uploads")
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
    log.info("test_parallel: uploaded %s in %s (%s/s)", 
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
    statitem = StatItem("upload", "test_parallel-%s" % tag, 
                        int(size), float(elapsed))
    stat.add(statitem)
