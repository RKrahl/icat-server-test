"""Ingest by directly writing a ZIP file to archive storage.
Parallel version: test how fast we can get data into IDS this way.
"""

from __future__ import print_function
import sys
import os
import os.path
import threading
if sys.version_info < (3, 0):
    import Queue as queue
else:
    import queue
import zlib
import logging
from timeit import default_timer as timer
import shutil
import pytest
import icat
import icat.config
from icat.query import Query
icat.hzb = pytest.importorskip("icat.hzb") # This test module is HZB specific
from icat.hzb.proposal import ProposalNo
from helper import Time, MemorySpace, DatasetBase, StatItem
from conftest import getConfig, getDatasetCount, wipe_data, callscript


log = logging.getLogger("test.%s" % __name__)

# ============================ testdata ============================

testProposalNo = "12100409-ST-1.1-P"
testDatasetPrefix = "test_parallel-ingest"

nThreadsParams = [1, 2, 3, 4, 6, 8, 10, 12, 16, 20]
numTests = len(nThreadsParams)

# ============================= helper =============================

@pytest.fixture(scope="module")
def icatconfig(setupicat, testConfig, request):
    client, conf, config = getConfig(ids="mandatory")
    mainbase = request.config.getini('mainstoragebase')
    archivebase = request.config.getini('archivestoragebase')
    conf.cmdargs.append("--mainStorageBase=%s" % mainbase)
    conf.cmdargs.append("--archiveStorageBase=%s" % archivebase)
    incomingbase = request.config.getini('incomingbase')
    proposaldir = os.path.join(incomingbase, testProposalNo.replace('/', '_'))
    os.mkdir(proposaldir)
    conf.proposaldir = proposaldir
    def cleanup():
        shutil.rmtree(proposaldir)
        client.login(conf.auth, conf.credentials)
        query = Query(client, "Dataset", conditions={
            "name": "LIKE '%s-%%'" % testDatasetPrefix
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
    return (conf, config)


class Datafile(object):

    def __init__(self, directory, fname, size):
        self.directory = directory
        self.fname = fname
        self.size = size
        self.path = os.path.join(self.directory, self.fname)
        self.crc32 = 0
        self._create()

    def _create(self):
        size = self.size
        chunksize = 8192
        with open("/dev/urandom", "rb") as infile:
            with open(self.path, "wb") as outfile:
                while size > 0:
                    if chunksize > size:
                        chunksize = size
                    chunk = infile.read(chunksize)
                    self.crc32 = zlib.crc32(chunk, self.crc32)
                    outfile.write(chunk)
                    size -= len(chunk)

    def getcrc(self):
        return "%x" % (self.crc32 & 0xffffffff)

    def unlink(self):
        os.unlink(self.path)


class Dataset(DatasetBase):

    fileCount = 4
    fileSize = MemorySpace("20 MiB")

    def __init__(self, incomingdir, proposal, name):
        self.proposal = ProposalNo.parse(proposal)
        self.name = name
        self.files = []
        self.datasetdir = os.path.join(incomingdir, name)
        os.mkdir(self.datasetdir)
        for n in range(1,self.fileCount+1):
            fname = "test_%05d.dat" % n
            datafile = Datafile(self.datasetdir, fname, self.fileSize)
            self.files.append(datafile)
        self.size = self.fileCount*self.fileSize
        self.dataset = None

    def uploadFiles(self, client):
        raise RuntimeError("This Dataset class does not support upload.")

    def ingest(self, conf):
        args = conf.cmdargs + [str(self.proposal), self.name]
        args.extend(f.path for f in self.files)
        start = timer()
        callscript("addfile-archive.py", args)
        end = timer()
        elapsed = Time(end - start)
        log.info("Ingest %s to dataset %s in %s (%s/s)", 
                 self.size, self.name, elapsed, MemorySpace(self.size/elapsed))
        return StatItem("ingest", self.name, int(self.size), float(elapsed))

    def verify(self, client):
        query = icat.query.Query(client, "Dataset",
                                 conditions={"name":"= '%s'" % self.name}, 
                                 includes=["datafiles"])
        cond = self.proposal.as_conditions()
        query.addConditions({ "investigation.%s" % a: c
                              for (a, c) in cond.items() })
        self.dataset = client.assertedSearch(query)[0]
        assert len(self.dataset.datafiles) == len(self.files), \
            "wrong number of datafiles"
        dfidx = dict([ (df.name,df) for df in self.dataset.datafiles ])
        for f in self.files:
            assert f.fname in dfidx
            df = dfidx[f.fname]
            assert df.location
            assert df.fileSize == f.size
            assert df.checksum == f.getcrc()

def createDatasets(testConfig, incomingdir, tag):
    dsCount = getDatasetCount(testConfig.baseSize, Dataset.getSize(), numTests)
    testDatasets = []
    for i in range(1, dsCount+1):
        name = "%s-%05d" % (tag, i)
        testDatasets.append(Dataset(incomingdir, testProposalNo, name))
        log.info("Created dataset %s in incoming.", name)
    return testDatasets

def checkResults(numDatasets, resultQueue, stat):
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
    assert c == numDatasets

# ============================= tests ==============================

@pytest.mark.parametrize("numThreads", nThreadsParams)
def test_ingest(testConfig, icatconfig, stat, numThreads):
    conf, config = icatconfig

    # ingest: that is what we actually want to test here.

    dsQueue = queue.Queue()
    resultQueue = queue.Queue()

    def ingestWorker(conf):
        while True:
            dataset = dsQueue.get()
            if dataset is None:
                break
            try:
                statitem = dataset.ingest(conf)
                resultQueue.put(statitem)
            except Exception as err:
                resultQueue.put(err)
            dsQueue.task_done()

    log.info("test_parallel-ingest: numThreads = %d", numThreads)
    tag = "%s-%02d" % (testDatasetPrefix, numThreads)
    testDatasets = createDatasets(testConfig, conf.proposaldir, tag)
    size = len(testDatasets) * Dataset.getSize()

    threads = []
    for i in range(numThreads):
        t = threading.Thread(target=ingestWorker, args=(conf,))
        t.start()
        threads.append(t)
    log.info("test_parallel-ingest: start ingest")
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
    log.info("test_parallel-ingest: ingested %s in %s (%s/s)", 
             size, elapsed, MemorySpace(size/elapsed))
    checkResults(len(testDatasets), resultQueue, stat)
    statitem = StatItem("ingest", tag, int(size), float(elapsed))
    stat.add(statitem)

    # verify and download: in order to verify that the ingest has been
    # done correctly, we verify the data in ICAT and download the
    # files back.  All datasets will need to be restored before
    # download, so this will take a significant amount of time.  So we
    # also do the download in parallel threads, just to speed things
    # up, even though we don't care too much to measure the parallel
    # performance here.

    dsQueue = queue.Queue()
    resultQueue = queue.Queue()

    def downloadWorker(conf, client_kwargs):
        client = icat.Client(conf.url, **client_kwargs)
        client.login(conf.auth, conf.credentials)
        while True:
            dataset = dsQueue.get()
            if dataset is None:
                break
            try:
                dataset.verify(client)
                statitem = dataset.download(client)
                resultQueue.put(statitem)
            except Exception as err:
                resultQueue.put(err)
            dsQueue.task_done()
        client.logout()

    threads = []
    for i in range(numThreads):
        t = threading.Thread(target=downloadWorker, 
                             args=(conf, config.client_kwargs))
        t.start()
        threads.append(t)
    log.info("test_parallel-ingest: verify and download datasets")
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
    log.info("test_parallel-ingest: download %s in %s (%s/s)", 
             size, elapsed, MemorySpace(size/elapsed))
    checkResults(len(testDatasets), resultQueue, stat)
    statitem = StatItem("download", tag, int(size), float(elapsed))
    stat.add(statitem)
