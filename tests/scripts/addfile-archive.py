#! /usr/bin/python
#
# Add a Dataset with some Datafiles to an Investigation.
#
# This script is intended as a proof of concept to ingest datafiles
# into IDS by directly copying a ZIP file of a dataset into the
# archive storage and registering the dataset and datafile objects in
# ICAT.
#
# $Id: addfile-archive.py 948 2017-02-17 09:20:47Z jsi $
#

import icat
import icat.config
import os
import os.path
import fcntl
import errno
from datetime import datetime
import zlib
import zipfile
import logging
import time
from icat.query import Query
from icat.hzb.storage import MainFileStorage, ArchiveFileStorage
from icat.hzb.proposal import ProposalNo
from icat.hzb.fileformats import FormatChecker

logging.basicConfig(level=logging.INFO)
#logging.getLogger('suds.client').setLevel(logging.DEBUG)
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

config = icat.config.Config()
config.add_variable('mainStorageBase', ("--mainStorageBase",), 
                    dict(help="base directory of the main storage area"))
config.add_variable('archiveStorageBase', ("--archiveStorageBase",), 
                    dict(help="base directory of the archive storage area"))
config.add_variable('datafileFormats', ("--datafileformats",), 
                    dict(help="configuration file for DatafileFormats"), 
                    default="%(configDir)s/fileformats.xml", subst=True)
config.add_variable('investigation', ("investigation",), 
                    dict(help="the proposal number"))
config.add_variable('dataset', ("dataset",), 
                    dict(help="name of the dataset"))
config.add_variable('files', ("files",), 
                    dict(help="name of the files to add", nargs="+"))
client, conf = config.getconfig()

client.login(conf.auth, conf.credentials)

mainStorage = MainFileStorage(conf.mainStorageBase)
archiveStorage = ArchiveFileStorage(conf.archiveStorageBase)

dff = FormatChecker(client, conf.datafileFormats)

os.umask(0o007)


# ------------------------------------------------------------
# class zipcreate.
# ------------------------------------------------------------

class zipcreate:
    """A context manager to create a ZipFile in a safe manner.

    Open the file for writing and take care not to overwrite any
    existing files.  Acquire an exclusive lock on the file.  Make sure
    it will finally get closed.
    """
    def __init__(self, fname):
        self.fname = fname
    def __enter__(self):
        try:
            os.makedirs(os.path.dirname(self.fname))
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        self.fd = os.open(self.fname, flags, 0o666)
        fcntl.lockf(self.fd, fcntl.LOCK_EX)
        self.f = os.fdopen(self.fd, 'wb')
        self.zipf = zipfile.ZipFile(self.f, 'w')
        return self.zipf
    def __exit__(self, type, value, tb):
        self.zipf.close()

# ------------------------------------------------------------
# Caclulate a crc32 checksum.
# ------------------------------------------------------------

def getcrc(fname):
    chunksize = 8192
    with open(fname, 'rb') as f:
        crc32 = 0
        while True:
            chunk = f.read(chunksize)
            if chunk:
                crc32 = zlib.crc32(chunk, crc32)
            else:
                break
    return crc32

# ------------------------------------------------------------
# Get the objects that we assume to be already present in ICAT.
# ------------------------------------------------------------

def getinvestigation(invid):
    proposal = ProposalNo.parse(invid)
    query = Query(client, "Investigation", 
                  conditions=proposal.as_conditions(),
                  includes={"facility"})
    return (client.assertedSearch(query)[0])

investigation = getinvestigation(conf.investigation)

datasettype = client.assertedSearch("DatasetType [name='raw']")[0]

# ====================================================================
# Do things in the right manner to be safe from data loss and to keep
# the storage always in a consistent state.  The order of operations
# is important.  We must first verify that the dataset directory does
# not exist in main storage and create & lock the ZIP file in archive
# storage.  Then we create the dataset together with the datafiles in
# the ICAT with one single create.  Finally we write the ZIP file.
# This way we guarantee that in the moment, the dataset pops into
# live, it is in ARCHIVED state (the main storage directory does not
# exist) and not empty (datafiles exist in the dataset).  Under these
# preconditions, any action in IDS concerning this dataset will
# trigger a restore first.  This restore will be blocked by the
# exclusive file lock we hold until the ZIP file is completely
# written.
# ====================================================================

# ------------------------------------------------------------
# Initialize the dataset, but do not create it yet.
# ------------------------------------------------------------

log.debug("start")

dataset = client.new("dataset")
dataset.name = conf.dataset
dataset.complete = False
dataset.investigation = investigation
dataset.type = datasettype

dsrelpath = mainStorage.getrelpath(dataset)
zippath = archiveStorage.getzippath(dataset)

datafiles = []
for fname in conf.files:
    basename = os.path.basename(fname)
    fstats = os.stat(fname)
    modTime = datetime.utcfromtimestamp(fstats.st_mtime).isoformat() + "Z"
    crc = getcrc(fname)
    datafile = client.new("datafile")
    datafile.name = basename
    datafile.datafileFormat = dff.getDatafileFormat(fname)
    datafile.datafileModTime = modTime
    datafile.datafileCreateTime = modTime
    datafile.fileSize = fstats.st_size
    datafile.checksum = "%x" % (crc & 0xffffffff)
    datafile.location = os.path.join(dsrelpath, basename)
    dataset.datafiles.append(datafile)
    datafiles.append( (fname, basename, datafile) )

# ------------------------------------------------------------
# Create the ZIP file and lock it.
# ------------------------------------------------------------

# Sanity check: the dataset directory MUST NOT exist in the main storage
dsabspath = mainStorage.abspath(dsrelpath)
if os.path.exists(dsabspath):
    raise RuntimeError("dataset directory %s already exists." % dsabspath)

log.debug("open ZIP file %s", zippath)
with zipcreate(zippath) as zipf:
    log.debug("ZIP file opened and locked")

    # --------------------------------------------------------
    # Create the dataset.
    # --------------------------------------------------------

    # Note: dataset.create() may fail if the dataset already exists.
    # We deliberately do not catch this error and in particular do not
    # unlink the ZIP file in case of the error as this would create a
    # race condition: another process (IDS) may already have
    # overwritten our file in the meanwhile.  Rather accept leaving an
    # empty file behind in case of the error.
    dataset.create()
    log.debug("dataset %s created", dataset.name)

    # --------------------------------------------------------
    # Write the ZIP file.
    # --------------------------------------------------------

    for (fname, basename, datafile) in datafiles:
        archivename = archiveStorage.getziparchivename(dataset, basename)
        log.debug("add file %s as %s to ZIP file", fname, archivename)
        zipf.write(fname, archivename)
        zinfo = zipf.getinfo(archivename)
        if zinfo.file_size != datafile.fileSize:
            raise RuntimeError("%s: Wrong size in ZIP (%d versus %d)" 
                               % (basename, zinfo.file_size, datafile.fileSize))
        zipcrc = "%x" % (zinfo.CRC & 0xffffffff)
        if zipcrc != datafile.checksum:
            raise RuntimeError("%s: Wrong checksum in ZIP (%s versus %s)" 
                               % (basename, zipcrc, datafile.checksum))

    log.debug("ZIP file written")

log.debug("done: ZIP file closed & lock released")
