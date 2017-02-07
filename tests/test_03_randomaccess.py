"""Test upload and download a large amount of data to and from IDS.
"""

from __future__ import print_function
import logging
from timeit import default_timer as timer
import random
import pytest
import icat
import icat.config
from icat.query import Query
from helper import Time, MemorySpace, DatasetBase
from conftest import getConfig, wipe_data


log = logging.getLogger("test.%s" % __name__)


# ============================ testdata ============================

testInvestigation = "12100409-ST"
testDatasetName = "test_randomaccess"
testDatasets = []

class SmallDataset(DatasetBase):
    fileCount = 4
    fileSize = MemorySpace("512 KiB")

class ManyFileDataset(DatasetBase):
    fileCount = 1000
    fileSize = MemorySpace("51.2 KiB")

class BigDataset(DatasetBase):
    fileCount = 2
    fileSize = MemorySpace("1 GiB")

def createDatasets(client, testConfig):
    query = Query(client, "Investigation", conditions={
        "name": "= '%s'" % testInvestigation,
    })
    inv = client.assertedSearch(query)[0]
    count = int(0.16*testConfig.baseSize/SmallDataset.getSize())
    for i in range(1, count+1):
        name = "%s-a%05d" % (testDatasetName, i)
        testDatasets.append(SmallDataset(client, inv, name))
    count = int(0.04*testConfig.baseSize/ManyFileDataset.getSize())
    for i in range(1, count+1):
        name = "%s-b%05d" % (testDatasetName, i)
        testDatasets.append(ManyFileDataset(client, inv, name))
    count = int(1.80*testConfig.baseSize/BigDataset.getSize())
    for i in range(1, count+1):
        name = "%s-c%05d" % (testDatasetName, i)
        testDatasets.append(BigDataset(client, inv, name))

# ============================= helper =============================

@pytest.fixture(scope="module")
def client(setupicat, testConfig, request):
    conf = getConfig(ids="mandatory")
    client = icat.Client(conf.url, **conf.client_kwargs)
    client.login(conf.auth, conf.credentials)
    def cleanup():
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
    createDatasets(client, testConfig)
    return client

# ============================= tests ==============================

def test_upload(client, testConfig, stat):
    hour = 60*60
    random.shuffle(testDatasets)
    totalsize = 0
    start = timer()
    next_refresh = 0
    for dataset in testDatasets:
        if timer() > next_refresh:
            client.refresh()
            next_refresh = timer()+hour
        statitem = dataset.uploadFiles(client)
        stat.add(statitem)
        totalsize += dataset.size
    end = timer()
    elapsed = Time(end - start)
    log.info("Uploaded %s in %s (%s/s)", 
             MemorySpace(totalsize), elapsed, MemorySpace(totalsize/elapsed))

def test_download(client, stat):
    hour = 60*60
    # Dowload each dataset five time in average
    count = 5*len(testDatasets)
    totalsize = 0
    start = timer()
    next_refresh = 0
    for i in range(count):
        if timer() > next_refresh:
            client.refresh()
            next_refresh = timer()+hour
        dataset = random.choice(testDatasets)
        statitem = dataset.download(client)
        stat.add(statitem)
        totalsize += dataset.size
    end = timer()
    elapsed = Time(end - start)
    log.info("Downloaded %s in %s (%s/s)", 
             MemorySpace(totalsize), elapsed, MemorySpace(totalsize/elapsed))
