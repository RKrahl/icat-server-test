"""Test upload and download a large amount of data to and from IDS.
"""

from __future__ import print_function
import logging
from timeit import default_timer as timer
import random
import time
import pytest
import icat
import icat.config
from icat.query import Query
from helper import Time, MemorySpace, DatasetBase
from conftest import getConfig, wipe_data


log = logging.getLogger("test.%s" % __name__)


# ============================ testdata ============================

testInvestigation = "gate1:12100409-ST-1.1-P"
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
    count = int(0.08*testConfig.baseSize/SmallDataset.getSize())
    for i in range(1, count+1):
        name = "%s-a%05d" % (testDatasetName, i)
        testDatasets.append(SmallDataset(client, inv, name))
    count = int(0.02*testConfig.baseSize/ManyFileDataset.getSize())
    for i in range(1, count+1):
        name = "%s-b%05d" % (testDatasetName, i)
        testDatasets.append(ManyFileDataset(client, inv, name))
    count = int(0.90*testConfig.baseSize/BigDataset.getSize())
    for i in range(1, count+1):
        name = "%s-c%05d" % (testDatasetName, i)
        testDatasets.append(BigDataset(client, inv, name))

# ============================= helper =============================

@pytest.fixture(scope="module")
def client(setupicat, testConfig, request):
    client, conf, _ = getConfig(ids="mandatory")
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
            client.logout()
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
    for dataset in testDatasets:
        client.autoRefresh()
        statitem = dataset.uploadFiles(client)
        stat.add(statitem)
        totalsize += dataset.size
    end = timer()
    elapsed = Time(end - start)
    log.info("Uploaded %s in %s (%s/s)", 
             MemorySpace(totalsize), elapsed, MemorySpace(totalsize/elapsed))
    # sleep a while to allow the uploaded datasets to be written to archive.
    time.sleep(2*60)

def test_download(client, stat):
    hour = 60*60
    # Dowload each dataset five time in average
    count = 5*len(testDatasets)
    totalsize = 0
    start = timer()
    for i in range(count):
        client.autoRefresh()
        dataset = random.choice(testDatasets)
        statitem = dataset.download(client)
        stat.add(statitem)
        totalsize += dataset.size
    end = timer()
    elapsed = Time(end - start)
    log.info("Downloaded %s in %s (%s/s)", 
             MemorySpace(totalsize), elapsed, MemorySpace(totalsize/elapsed))
