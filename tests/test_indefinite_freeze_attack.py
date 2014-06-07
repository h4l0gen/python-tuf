#!/usr/bin/env python

"""
<Program Name>
  test_indefinite_freeze_attack.py

<Author>
  Konstantin Andrianov.

<Started>
  March 10, 2012.

  April 1, 2014.
    Refactored to use the 'unittest' module (test conditions in code, rather
    than verifying text output), use pre-generated repository files, and
    discontinue use of the old repository tools. -vladimir.v.diaz

<Copyright>
  See LICENSE for licensing information.

<Purpose>
  Simulate an indefinite freeze attack.  In an indefinite freeze attack,
  attacker is able to respond to client's requests with the same, outdated
  metadata without the client being aware.
"""

# Help with Python 3 compatibility, where the print statement is a function, an
# implicit relative import is invalid, and the '/' operator performs true
# division.  Example:  print 'hello world' raises a 'SyntaxError' exception.
from __future__ import print_function
from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals

import os
import random
import time
import tempfile
import shutil
import json
import subprocess
import logging
import sys

# 'unittest2' required for testing under Python < 2.7.
if sys.version_info >= (2, 7):
  import unittest

else:
  import unittest2 as unittest 

import tuf.formats
import tuf.util
import tuf.log
import tuf.client.updater as updater
import tuf.repository_tool as repo_tool
import tuf.unittest_toolbox as unittest_toolbox
import tuf._vendor.six as six

# The repository tool is imported and logs console messages by default.  Disable
# console log messages generated by this unit test.
repo_tool.disable_console_log_messages()

logger = logging.getLogger('tuf.test_indefinite_freeze_attack')


class TestIndefiniteFreezeAttack(unittest_toolbox.Modified_TestCase):

  @classmethod
  def setUpClass(cls):
    # setUpClass() is called before any of the test cases are executed.
    
    # Create a temporary directory to store the repository, metadata, and target
    # files.  'temporary_directory' must be deleted in TearDownModule() so that
    # temporary files are always removed, even when exceptions occur. 
    cls.temporary_directory = tempfile.mkdtemp(dir=os.getcwd())
    
    # Launch a SimpleHTTPServer (serves files in the current directory).
    # Test cases will request metadata and target files that have been
    # pre-generated in 'tuf/tests/repository_data', which will be served by the
    # SimpleHTTPServer launched here.  The test cases of this unit test assume 
    # the pre-generated metadata files have a specific structure, such
    # as a delegated role 'targets/role1', three target files, five key files,
    # etc.
    cls.SERVER_PORT = random.randint(30000, 45000)
    command = ['python', 'simple_server.py', str(cls.SERVER_PORT)]
    cls.server_process = subprocess.Popen(command, stderr=subprocess.PIPE)
    logger.info('Server process started.')
    logger.info('Server process id: '+str(cls.server_process.pid))
    logger.info('Serving on port: '+str(cls.SERVER_PORT))
    cls.url = 'http://localhost:'+str(cls.SERVER_PORT) + os.path.sep

    # NOTE: Following error is raised if a delay is not applied:
    # <urlopen error [Errno 111] Connection refused>
    time.sleep(.2)



  @classmethod 
  def tearDownClass(cls):
    # tearDownModule() is called after all the test cases have run.
    # http://docs.python.org/2/library/unittest.html#class-and-module-fixtures
   
    # Remove the temporary repository directory, which should contain all the
    # metadata, targets, and key files generated of all the test cases.
    shutil.rmtree(cls.temporary_directory)
    
    # Kill the SimpleHTTPServer process.
    if cls.server_process.returncode is None:
      logger.info('Server process '+str(cls.server_process.pid)+' terminated.')
      cls.server_process.kill()



  def setUp(self):
    # We are inheriting from custom class.
    unittest_toolbox.Modified_TestCase.setUp(self)
  
    # Copy the original repository files provided in the test folder so that
    # any modifications made to repository files are restricted to the copies.
    # The 'repository_data' directory is expected to exist in 'tuf/tests/'.
    original_repository_files = os.path.join(os.getcwd(), 'repository_data') 
    temporary_repository_root = \
      self.make_temp_directory(directory=self.temporary_directory)
  
    # The original repository, keystore, and client directories will be copied
    # for each test case. 
    original_repository = os.path.join(original_repository_files, 'repository')
    original_client = os.path.join(original_repository_files, 'client')
    original_keystore = os.path.join(original_repository_files, 'keystore')
    
    # Save references to the often-needed client repository directories.
    # Test cases need these references to access metadata and target files. 
    self.repository_directory = \
      os.path.join(temporary_repository_root, 'repository')
    self.client_directory = os.path.join(temporary_repository_root, 'client')
    self.keystore_directory = os.path.join(temporary_repository_root, 'keystore')
    
    # Copy the original 'repository', 'client', and 'keystore' directories
    # to the temporary repository the test cases can use.
    shutil.copytree(original_repository, self.repository_directory)
    shutil.copytree(original_client, self.client_directory)
    shutil.copytree(original_keystore, self.keystore_directory)
    
    # Set the url prefix required by the 'tuf/client/updater.py' updater.
    # 'path/to/tmp/repository' -> 'localhost:8001/tmp/repository'. 
    repository_basepath = self.repository_directory[len(os.getcwd()):]
    url_prefix = \
      'http://localhost:' + str(self.SERVER_PORT) + repository_basepath 
    
    # Setting 'tuf.conf.repository_directory' with the temporary client
    # directory copied from the original repository files.
    tuf.conf.repository_directory = self.client_directory 
    self.repository_mirrors = {'mirror1': {'url_prefix': url_prefix,
                                           'metadata_path': 'metadata',
                                           'targets_path': 'targets',
                                           'confined_target_dirs': ['']}}

    # Create the repository instance.  The test cases will use this client
    # updater to refresh metadata, fetch target files, etc.
    self.repository_updater = updater.Updater('test_repository',
                                              self.repository_mirrors)


  def tearDown(self):
    # Modified_TestCase.tearDown() automatically deletes temporary files and
    # directories that may have been created during each test case.
    unittest_toolbox.Modified_TestCase.tearDown(self)



  def test_without_tuf(self):
    # Scenario:
    # 'timestamp.json' specifies the latest version of the repository files.
    # A client should only accept the same version of this file up to a certain
    # point, or else it cannot detect that new files are available for download.
    # Modify the repository's timestamp.json' so that it expires soon, copy it
    # over the to client, and attempt to re-fetch the same expired version. 
    # A non-TUF client (without a way to detect when metadata has expired) is
    # expected to download the same version, and thus the same outdated files.
    # Verify that the same file size and hash of 'timestamp.json' is downloaded.

    timestamp_path = os.path.join(self.repository_directory, 'metadata',
                                  'timestamp.json')

    timestamp_metadata = tuf.util.load_json_file(timestamp_path)
    expires = tuf.formats.unix_timestamp_to_datetime(int(time.time() - 10))
    expires = expires.isoformat() + 'Z'
    timestamp_metadata['signed']['expires'] = expires 
    tuf.formats.check_signable_object_format(timestamp_metadata) 
    
    with open(timestamp_path, 'wb') as file_object:
      # Explicitly specify the JSON separators for Python 2 + 3 consistency.
      timestamp_content = \
        json.dumps(timestamp_metadata, indent=1, separators=(',', ': '),
                   sort_keys=True).encode('utf-8')
      file_object.write(timestamp_content)

    client_timestamp_path = os.path.join(self.client_directory,
                                         'timestamp.json')
    shutil.copy(timestamp_path, client_timestamp_path)
    
    length, hashes = tuf.util.get_file_details(timestamp_path)
    fileinfo = tuf.formats.make_fileinfo(length, hashes) 
    
    url_prefix = self.repository_mirrors['mirror1']['url_prefix']
    url_file = os.path.join(url_prefix, 'metadata', 'timestamp.json')
   
    six.moves.urllib.request.urlretrieve(url_file, client_timestamp_path)
    
    length, hashes = tuf.util.get_file_details(client_timestamp_path)
    download_fileinfo = tuf.formats.make_fileinfo(length, hashes)
    
    # Verify 'download_fileinfo' is equal to the current local file.
    self.assertEqual(download_fileinfo, fileinfo)



  def test_with_tuf(self):
    # The same scenario outlined in test_without_tuf() is followed here, except
    # with a TUF client.  The TUF client performs a refresh of top-level
    # metadata, which also includes 'timestamp.json'.
    
    timestamp_path = os.path.join(self.repository_directory, 'metadata',
                                  'timestamp.json')
    
    # Modify the timestamp file on the remote repository.  'timestamp.json'
    # must be properly updated and signed with 'repository_tool.py', otherwise
    # the client will reject it as invalid metadata.  The resulting
    # 'timestamp.json' should be valid metadata, but expired (as intended).
    repository = repo_tool.load_repository(self.repository_directory)
 
    key_file = os.path.join(self.keystore_directory, 'timestamp_key') 
    timestamp_private = repo_tool.import_rsa_privatekey_from_file(key_file,
                                                                  'password')

    repository.timestamp.load_signing_key(timestamp_private)
    
    # expire in 1 second.
    datetime_object = tuf.formats.unix_timestamp_to_datetime(int(time.time() + 1))
    repository.timestamp.expiration = datetime_object
    repository.write()
    
    # Move the staged metadata to the "live" metadata.
    shutil.rmtree(os.path.join(self.repository_directory, 'metadata'))
    shutil.copytree(os.path.join(self.repository_directory, 'metadata.staged'),
                    os.path.join(self.repository_directory, 'metadata'))
    
    # Verify that the TUF client detects outdated metadata and refuses to
    # continue the update process.  Sleep for at least 2 seconds to ensure
    # 'repository.timestamp.expiration' is reached.
    time.sleep(2)
    try:
      self.repository_updater.refresh()
    
    except tuf.NoWorkingMirrorError as e:
      for mirror_url, mirror_error in six.iteritems(e.mirror_errors):
        self.assertTrue(isinstance(mirror_error, tuf.ExpiredMetadataError))


if __name__ == '__main__':
  unittest.main()