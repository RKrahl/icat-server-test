"""pytest configuration.
"""

from __future__ import print_function
import os.path
import logging
import pytest
import icat
import icat.config


# Note that pytest captures stderr, so we won't see any logging by
# default.  But since Suds uses logging, it's better to still have
# a well defined basic logging configuration in place.
logging.basicConfig(level=logging.INFO)

testdir = os.path.dirname(__file__)


# ============================= helper ===============================

def getConfig(confSection="root", **confArgs):
    """Get the configuration, skip on ConfigError.
    """
    confFile = os.path.join(testdir, "data", "icat.cfg")
    if not os.path.isfile(confFile):
        pytest.skip("no test ICAT server configured")
    try:
        args = ["-c", confFile, "-s", confSection]
        conf = icat.config.Config(**confArgs).getconfig(args)
        conf.cmdargs = ["-c", conf.configFile[0], "-s", conf.configSection]
        return conf
    except icat.ConfigError as err:
        pytest.skip(err.message)
