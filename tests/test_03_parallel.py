"""Try to upload and download as fast as possible using mutliple threads.
"""

from __future__ import print_function
import threading
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
testDatasets = []

class Dataset(DatasetBase):
    fileCount = 4
    fileSize = MemorySpace("20 MiB")

def createDatasets(client, testConfig):
    query = Query(client, "Investigation", conditions={
        "name": "= '%s'" % testInvestigation,
    })
    inv = client.assertedSearch(query)[0]
    count = int(10*testConfig.baseSize/Dataset.getSize())
    for i in range(1, count+1):
        name = "%s-%05d" % (testConfig.moduleName, i)
        testDatasets.append(Dataset(client, inv, name))

# ============================= helper =============================

@pytest.fixture(scope="module")
def icatconfig(setupicat, testConfig, request):
    conf = getConfig(ids="mandatory")
    client = icat.Client(conf.url, **conf.client_kwargs)
    client.login(conf.auth, conf.credentials)
    def cleanup():
        query = Query(client, "Dataset", conditions={
            "name": "LIKE '%s-%%'" % testConfig.moduleName
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
    createDatasets(client, testConfig)
    return conf

# ============================= tests ==============================

def test_upload(icatconfig, testConfig, stat):

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

    threads = []
    for i in range(testConfig.numThreads):
        t = threading.Thread(target=uploadWorker, args=(icatconfig,))
        t.start()
        threads.append(t)
    for dataset in testDatasets:
        dsQueue.put(dataset)
    dsQueue.join()
    for i in range(testConfig.numThreads):
        dsQueue.put(None)
    for t in threads:
        t.join()
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


def test_download(icatconfig, testConfig, stat):

    dsQueue = queue.Queue()
    resultQueue = queue.Queue()

    def downloadWorker(conf):
        client = icat.Client(conf.url, **conf.client_kwargs)
        client.login(conf.auth, conf.credentials)
        while True:
            dataset = dsQueue.get()
            if dataset is None:
                break
            try:
                statitem = dataset.download(client)
                resultQueue.put(statitem)
            except Exception as err:
                resultQueue.put(err)
            dsQueue.task_done()
        client.logout()

    count = 5*len(testDatasets)
    threads = []
    for i in range(testConfig.numThreads):
        t = threading.Thread(target=downloadWorker, args=(icatconfig,))
        t.start()
        threads.append(t)
    for i in range(count):
        dsQueue.put(random.choice(testDatasets))
    dsQueue.join()
    for i in range(testConfig.numThreads):
        dsQueue.put(None)
    for t in threads:
        t.join()
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
    assert c == count
