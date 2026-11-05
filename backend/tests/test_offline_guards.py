"""Ensure the offline runner fails closed and restores its process patches."""

import builtins
import importlib
import io
import socket
import sys
import types
import unittest
from unittest.mock import Mock, patch

import pymysql

from run_tests import MODEL_DIRS, MODEL_PACKAGES, RejectModelImports, guarded_open, offline_guards


class OfflineGuardTests(unittest.TestCase):
    def test_model_dependencies_are_rejected_but_standard_library_is_allowed(self):
        finder = RejectModelImports()
        for name in MODEL_PACKAGES:
            with self.subTest(name=name), self.assertRaises(AssertionError):
                finder.find_spec(name + '.submodule')
        self.assertIsNone(finder.find_spec('json'))

    def test_explicit_model_doubles_can_be_imported(self):
        double = types.ModuleType('transformers')
        with offline_guards(), patch.dict(sys.modules, {'transformers': double}):
            self.assertIs(importlib.import_module('transformers'), double)

    def test_model_directory_files_are_rejected_before_open(self):
        original = Mock()
        guarded = guarded_open(original)
        for directory in MODEL_DIRS:
            with self.subTest(directory=directory), self.assertRaises(AssertionError):
                guarded(directory / 'nested' / 'weights.bin', 'rb')
        original.assert_not_called()

    def test_other_files_and_file_descriptors_are_forwarded_unchanged(self):
        original = Mock()
        guarded = guarded_open(original)
        self.assertIs(guarded(__file__, 'rb'), original.return_value)
        original.assert_called_once_with(__file__, 'rb')
        original.reset_mock()
        guarded(10, 'rb', closefd=False)
        original.assert_called_once_with(10, 'rb', closefd=False)

    def test_database_network_and_file_guards_are_scoped(self):
        originals = (builtins.open, io.open, socket.create_connection, socket.socket.connect,
                     pymysql.connect, sys.meta_path, sys.path)
        with offline_guards():
            for function, arguments in (
                (pymysql.connect, ()),
                (socket.create_connection, (('127.0.0.1', 3306),)),
                (socket.getaddrinfo, ('localhost', 80)),
            ):
                with self.subTest(function=function), self.assertRaises(AssertionError):
                    function(*arguments)
            with socket.socket() as client:
                with self.assertRaises(AssertionError):
                    client.connect(('127.0.0.1', 3306))
                with self.assertRaises(AssertionError):
                    client.connect_ex(('127.0.0.1', 3306))
                with self.assertRaises(AssertionError):
                    client.sendto(b'test', ('127.0.0.1', 80))
            for opener in (builtins.open, io.open):
                with self.subTest(opener=opener), self.assertRaises(AssertionError):
                    opener(MODEL_DIRS[0] / 'weights.bin', 'rb')
        restored = (builtins.open, io.open, socket.create_connection, socket.socket.connect,
                    pymysql.connect, sys.meta_path, sys.path)
        for before, after in zip(originals, restored):
            self.assertIs(before, after)
