"""Test upload and download of files to and from IDS.
"""

from __future__ import print_function
import pytest
import icat
import icat.config
from icat.query import Query
from conftest import getConfig, wipe_data, DatasetBase, MemorySpace


# ============================ testdata ============================

testInvestigation = "12100409-ST"
testDatasets = []

# ============================= helper =============================

def createDatasets(client, testConfig):
    query = Query(client, "Investigation", conditions={
        "name": "= '%s'" % testInvestigation,
    })
    inv = client.assertedSearch(query)[0]
    testFCount = 10
    testFSize = MemorySpace(testConfig.baseSize // (10*testFCount))
    for data in ['random', 'zero', 'urandom']:
        name = "%s-%s" % (testConfig.moduleName, data)
        testDatasets.append(DatasetBase(client, inv, name, 
                                        testFCount, testFSize, data))

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
        client.deleteMany(client.search(query))
    if testConfig.cleanup:
        request.addfinalizer(cleanup)
    createDatasets(client, testConfig)
    return client

# ============================= tests ==============================

def test_upload(client, testConfig, stat):
    for dataset in testDatasets:
        statitem = dataset.uploadFiles(client)
        stat.add(statitem)

def test_download(client, stat):
    for dataset in testDatasets:
        statitem = dataset.download(client)
        stat.add(statitem)
