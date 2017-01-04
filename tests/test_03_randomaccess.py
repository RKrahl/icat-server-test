"""Test upload a large amount of dataand download of files to and from IDS.
"""

from __future__ import print_function
import logging
from timeit import default_timer as timer
import random
import pytest
import icat
import icat.config
from icat.query import Query
from conftest import getConfig, wipe_data, Dataset, Time, MemorySpace


log = logging.getLogger("test.%s" % __name__)


# ============================ testdata ============================

testInvestigation = "12100409-ST"
testDatasets = []

class SmallDataset(Dataset):
    size = 4*MemorySpace("512 KiB")
    def __init__(self, client, inv, name):
        s = MemorySpace("512 KiB")
        super(SmallDataset, self).__init__(client, inv, name, 4, s)

class ManyFileDataset(Dataset):
    size = 1000*MemorySpace("51.2 KiB")
    def __init__(self, client, inv, name):
        s = MemorySpace("51.2 KiB")
        super(ManyFileDataset, self).__init__(client, inv, name, 1000, s)

class BigDataset(Dataset):
    size = 2*MemorySpace("1 GiB")
    def __init__(self, client, inv, name):
        s = MemorySpace("1 GiB")
        super(BigDataset, self).__init__(client, inv, name, 2, s)

def createDatasets(client, testConfig):
    query = Query(client, "Investigation", conditions={
        "name": "= '%s'" % testInvestigation,
    })
    inv = client.assertedSearch(query)[0]
    count = int(0.8*testConfig.baseSize/SmallDataset.size)
    for i in range(1, count+1):
        name = "%s-a%05d" % (testConfig.moduleName, i)
        testDatasets.append(SmallDataset(client, inv, name))
    count = int(0.2*testConfig.baseSize/ManyFileDataset.size)
    for i in range(1, count+1):
        name = "%s-b%05d" % (testConfig.moduleName, i)
        testDatasets.append(ManyFileDataset(client, inv, name))
    count = int(9.0*testConfig.baseSize/BigDataset.size)
    for i in range(1, count+1):
        name = "%s-c%05d" % (testConfig.moduleName, i)
        testDatasets.append(BigDataset(client, inv, name))

# ============================= helper =============================

@pytest.fixture(scope="module")
def client(setupicat, testConfig, request):
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
    return client

# ============================= tests ==============================

def test_upload(client, testConfig):
    random.shuffle(testDatasets)
    totalsize = 0
    start = timer()
    for dataset in testDatasets:
        dataset.uploadFiles(client)
        totalsize += dataset.size
    end = timer()
    elapsed = Time(end - start)
    log.info("Uploaded %s in %s (%s/s)", 
             MemorySpace(totalsize), elapsed, MemorySpace(totalsize/elapsed))

def test_download(client):
    # Dowload each dataset five time in average
    count = 5*len(testDatasets)
    totalsize = 0
    start = timer()
    for i in range(count):
        dataset = random.choice(testDatasets)
        dataset.download(client)
        totalsize += dataset.size
    end = timer()
    elapsed = Time(end - start)
    log.info("Downloaded %s in %s (%s/s)", 
             MemorySpace(totalsize), elapsed, MemorySpace(totalsize/elapsed))
