"""Ingest by directly writing a ZIP file to archive storage.
"""

from __future__ import print_function
import sys
import os
import os.path
import re
import time
from threading import Thread
if sys.version_info < (3, 0):
    import Queue as queue
else:
    import queue
import zlib
import logging
import pytest
import icat
import icat.config
from icat.query import Query
from icat.ids import DataSelection
from icat.hzb.proposal import ProposalNo
from helper import MemorySpace, DatasetBase
from conftest import getConfig, callscript


log = logging.getLogger("test.%s" % __name__)

# ============================ testdata ============================

testProposalNo = "12100409-ST-1.1-P"
testDatasetPrefix = "test_ingestarchive"

# ============================= helper =============================


@pytest.fixture(scope="module")
def icatconfig(setupicat, testConfig, request):
    client, conf, config = getConfig(ids="mandatory")
    client.login(conf.auth, conf.credentials)
    mainbase = request.config.getini('mainstoragebase')
    archivebase = request.config.getini('archivestoragebase')
    conf.cmdargs.append("--mainStorageBase=%s" % mainbase)
    conf.cmdargs.append("--archiveStorageBase=%s" % archivebase)
    incomingbase = request.config.getini('incomingbase')
    proposaldir = os.path.join(incomingbase, testProposalNo.replace('/', '_'))
    os.mkdir(proposaldir)
    conf.proposaldir = proposaldir
    def cleanup():
        os.rmdir(proposaldir)
        client.logout()
    if testConfig.cleanup:
        request.addfinalizer(cleanup)
    return (client, conf, config)

@pytest.fixture(scope="function")
def dataset(request, icatconfig, testConfig):
    client, conf, _ = icatconfig
    assert request.node.name.startswith("test_")
    name = testDatasetPrefix + request.node.name[4:]
    name = re.sub(r'[\[\]]', '_', name)
    dataset = Dataset(conf.proposaldir, testProposalNo, name)
    def cleanup():
        dataset.cleanup(client)
    if testConfig.cleanup:
        request.addfinalizer(cleanup)
    return dataset


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

    fileCount = 32
    fileSize = MemorySpace("10 MiB")

    def __init__(self, incomingdir, proposal, name):
        self.proposal = ProposalNo(proposal)
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

    def cleanup(self, client):
        super(Dataset, self).cleanup(client)
        for f in self.files:
            f.unlink()
        os.rmdir(self.datasetdir)

    def uploadFiles(self, client):
        raise RuntimeError("This Dataset class does not support upload.")

    def ingest(self, conf):
        args = conf.cmdargs + [str(self.proposal), self.name]
        args.extend(f.path for f in self.files)
        log.info("%s: call ingest script", self.name)
        callscript("addfile-archive.py", args)
        log.info("%s: ingest complete", self.name)

    def search(self, client):
        query = icat.query.Query(client, "Dataset",
                                 conditions={"name":"= '%s'" % self.name}, 
                                 includes=["datafiles"])
        query.addConditions({"investigation.name":"='%s'" % self.proposal.name})
        if self.proposal.visitId:
            query.addConditions({"investigation.visitId":
                                 "='%s'" % self.proposal.visitId})
        return client.assertedSearch(query)[0]

    def verify(self, client):
        self.dataset = self.search(client)
        assert len(self.dataset.datafiles) == len(self.files), \
            "wrong number of datafiles"
        dfidx = dict([ (df.name,df) for df in self.dataset.datafiles ])
        for f in self.files:
            assert f.fname in dfidx
            df = dfidx[f.fname]
            assert df.location
            assert df.fileSize == f.size
            assert df.checksum == f.getcrc()

# ============================= tests ==============================

def test_ingest(icatconfig, dataset):
    """Ingest a dataset by directly writing to archive storage.
    """
    client, conf, _ = icatconfig
    dataset.ingest(conf)
    dataset.verify(client)
    dataset.download(client)

def test_ingest_and_upload(icatconfig, dataset):
    """Try to upload a file to the same dataset while we are ingesting it.
    """
    client, conf, config = icatconfig

    resultQueue = queue.Queue()

    def upload(conf, client_kwargs, dataset):
        try:
            # Note: I'm not sure whether Suds is thread safe.  Therefore
            # use a separate local client object in each thread.
            client = icat.Client(conf.url, **client_kwargs)
            client.login(conf.auth, conf.credentials)
            name = dataset.name
            datafileFormat = dataset.getDatafileFormat(client)
            f = Datafile(dataset.datasetdir, "upload.dat", 32)
            while True:
                # Get the dataset object from ICAT, continue to retry
                # while it does not exist yet.
                try:
                    ds = dataset.search(client)
                    log.info("Upload: dataset %s found.", name)
                    break
                except icat.SearchAssertionError:
                    log.info("Upload: dataset %s not found (yet).", name)
                    time.sleep(0.2)
                    continue
            selection = DataSelection([ds])
            datafile = client.new("datafile", name=f.fname, 
                                  dataset=ds, datafileFormat=datafileFormat)
            while True:
                # Do the upload.  This may (or even should) fail due to
                # the dataset not online.  The error triggers a restore,
                # so we continue to retry until the restore has been
                # completed.
                try:
                    df = client.putData(f.path, datafile)
                    log.info("Upload to dataset %s succeeded.", name)
                    break
                except icat.IDSDataNotOnlineError:
                    status = client.ids.getStatus(selection)
                    log.info("Upload: dataset %s is %s.", name, status)
                    time.sleep(0.2)
                    continue
            resultQueue.put(f)
            client.logout()
        except Exception as err:
            resultQueue.put(err)

    def ingest(conf, dataset):
        try:
            dataset.ingest(conf)
            resultQueue.put("Ok")
        except Exception as err:
            resultQueue.put(err)

    tul = Thread(target=upload, args=(conf, config.client_kwargs, dataset))
    tin = Thread(target=ingest, args=(conf, dataset))
    threads = [tul, tin]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    c = 0
    while True:
        try:
            r = resultQueue.get(block=False)
            c += 1
            if isinstance(r, Datafile):
                dataset.files.append(r)
                dataset.fileCount += 1
            elif isinstance(r, str):
                pass
            else:
                raise r
        except queue.Empty:
            break
    assert c == 2
    dataset.verify(client)
    dataset.download(client)

@pytest.mark.parametrize("delay", [False, True])
def test_ingest_existing(icatconfig, dataset, delay):
    """Try to ingest a dataset that already exist.  This must yield an
    error and must not in any way damage the already existing dataset.
    """
    client, conf, _ = icatconfig
    query = icat.query.Query(client, "Investigation")
    query.addConditions({"name":"='%s'" % dataset.proposal.name})
    if dataset.proposal.visitId:
        query.addConditions({"visitId": "='%s'" % dataset.proposal.visitId})
    investigation = client.assertedSearch(query)[0]
    old_dataset = DatasetBase(client, investigation, dataset.name, 
                              fileCount=4, fileSize=MemorySpace("10 KiB"))
    old_dataset.uploadFiles(client)
    if delay:
        # Request archive of the old dataset and wait until it is
        # written to archive storage and removed from main storage.
        client.ids.archive(DataSelection([old_dataset.dataset]))
        time.sleep(90)
    # OSError is raised if the ZIP file in archive storage exists,
    # RuntimeError is raised if the directory in main storage exists,
    # icat.ICATObjectExistsError is raised if neither files exist, but
    # the dataset in ICAT.
    with pytest.raises( (OSError, RuntimeError, icat.ICATObjectExistsError) ):
        dataset.ingest(conf)
    old_dataset.download(client)
    old_dataset.cleanup()
