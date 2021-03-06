# coding=utf-8

# Copyright 2012 Hewlett-Packard Development Company, L.P.
# Copyright (c) 2012 NTT DOCOMO, INC.
# Copyright 2014 International Business Machines Corporation
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Test class for IPMITool driver module."""

import mock
import os
import stat
import tempfile

from oslo.config import cfg

from ironic.common import driver_factory
from ironic.common import exception
from ironic.common import states
from ironic.common import utils
from ironic.conductor import task_manager
from ironic.db import api as db_api
from ironic.drivers.modules import console_utils
from ironic.drivers.modules import ipmitool as ipmi
from ironic.openstack.common import context
from ironic.openstack.common import processutils
from ironic.tests import base
from ironic.tests.conductor import utils as mgr_utils
from ironic.tests.db import base as db_base
from ironic.tests.db import utils as db_utils
from ironic.tests.objects import utils as obj_utils

CONF = cfg.CONF

INFO_DICT = db_utils.get_test_ipmi_info()


class IPMIToolPrivateMethodTestCase(base.TestCase):

    def setUp(self):
        super(IPMIToolPrivateMethodTestCase, self).setUp()
        self.context = context.get_admin_context()
        self.node = obj_utils.get_test_node(
                self.context,
                driver='fake_ipmitool',
                driver_info=INFO_DICT)
        self.info = ipmi._parse_driver_info(self.node)

    def test__make_password_file(self):
        with ipmi._make_password_file(self.info.get('password')) as pw_file:
            del_chk_pw_file = pw_file
            self.assertTrue(os.path.isfile(pw_file))
            self.assertEqual(0o600, os.stat(pw_file)[stat.ST_MODE] & 0o777)
            with open(pw_file, "r") as f:
                password = f.read()
            self.assertEqual(self.info.get('password'), password)
        self.assertFalse(os.path.isfile(del_chk_pw_file))

    def test__parse_driver_info(self):
        # make sure we get back the expected things
        self.assertIsNotNone(self.info.get('address'))
        self.assertIsNotNone(self.info.get('username'))
        self.assertIsNotNone(self.info.get('password'))
        self.assertIsNotNone(self.info.get('uuid'))

        info = dict(INFO_DICT)

        # test the default value for 'priv_level'
        node = obj_utils.get_test_node(self.context, driver_info=info)
        ret = ipmi._parse_driver_info(node)
        self.assertEqual('ADMINISTRATOR', ret['priv_level'])

        # ipmi_username / ipmi_password are not mandatory
        del info['ipmi_username']
        node = obj_utils.get_test_node(self.context, driver_info=info)
        ipmi._parse_driver_info(node)
        del info['ipmi_password']
        node = obj_utils.get_test_node(self.context, driver_info=info)
        ipmi._parse_driver_info(node)

        # make sure error is raised when ipmi_address is missing
        del info['ipmi_address']
        node = obj_utils.get_test_node(self.context, driver_info=info)
        self.assertRaises(exception.InvalidParameterValue,
                          ipmi._parse_driver_info,
                          node)

        # test the invalid priv_level value
        self.info['priv_level'] = 'ABCD'
        node = obj_utils.get_test_node(self.context, driver_info=info)
        self.assertRaises(exception.InvalidParameterValue,
                          ipmi._parse_driver_info,
                          node)

    def test__exec_ipmitool(self):
        pw_file_handle = tempfile.NamedTemporaryFile()
        pw_file = pw_file_handle.name
        file_handle = open(pw_file, "w")
        args = [
            'ipmitool',
            '-I', 'lanplus',
            '-H', self.info['address'],
            '-L', self.info['priv_level'],
            '-U', self.info['username'],
            '-f', file_handle,
            'A', 'B', 'C',
            ]

        with mock.patch.object(ipmi, '_make_password_file',
                               autospec=True) as mock_pwf:
            mock_pwf.return_value = file_handle
            with mock.patch.object(utils, 'execute',
                                   autospec=True) as mock_exec:
                mock_exec.return_value = (None, None)

                ipmi._exec_ipmitool(self.info, 'A B C')

                mock_pwf.assert_called_once_with(self.info['password'])
                mock_exec.assert_called_once_with(*args, attempts=3)

    def test__exec_ipmitool_without_password(self):
        self.info['password'] = None
        pw_file_handle = tempfile.NamedTemporaryFile()
        pw_file = pw_file_handle.name
        file_handle = open(pw_file, "w")
        args = [
            'ipmitool',
            '-I', 'lanplus',
            '-H', self.info['address'],
            '-L', self.info['priv_level'],
            '-U', self.info['username'],
            '-f', file_handle,
            'A', 'B', 'C',
            ]

        with mock.patch.object(ipmi, '_make_password_file',
                               autospec=True) as mock_pwf:
            mock_pwf.return_value = file_handle
            with mock.patch.object(utils, 'execute',
                                   autospec=True) as mock_exec:
                mock_exec.return_value = (None, None)
                ipmi._exec_ipmitool(self.info, 'A B C')
                self.assertTrue(mock_pwf.called)
                mock_exec.assert_called_once_with(*args, attempts=3)

    def test__exec_ipmitool_without_username(self):
        self.info['username'] = None
        pw_file_handle = tempfile.NamedTemporaryFile()
        pw_file = pw_file_handle.name
        file_handle = open(pw_file, "w")
        args = [
            'ipmitool',
            '-I', 'lanplus',
            '-H', self.info['address'],
            '-L', self.info['priv_level'],
            '-f', file_handle,
            'A', 'B', 'C',
            ]

        with mock.patch.object(ipmi, '_make_password_file',
                               autospec=True) as mock_pwf:
            mock_pwf.return_value = file_handle
            with mock.patch.object(utils, 'execute',
                                   autospec=True) as mock_exec:
                mock_exec.return_value = (None, None)
                ipmi._exec_ipmitool(self.info, 'A B C')
                self.assertTrue(mock_pwf.called)
                mock_exec.assert_called_once_with(*args, attempts=3)

    def test__exec_ipmitool_exception(self):
        pw_file_handle = tempfile.NamedTemporaryFile()
        pw_file = pw_file_handle.name
        file_handle = open(pw_file, "w")
        args = [
            'ipmitool',
            '-I', 'lanplus',
            '-H', self.info['address'],
            '-L', self.info['priv_level'],
            '-U', self.info['username'],
            '-f', file_handle,
            'A', 'B', 'C',
            ]

        with mock.patch.object(ipmi, '_make_password_file',
                               autospec=True) as mock_pwf:
            mock_pwf.return_value = file_handle
            with mock.patch.object(utils, 'execute',
                                   autospec=True) as mock_exec:
                mock_exec.side_effect = processutils.ProcessExecutionError("x")
                self.assertRaises(processutils.ProcessExecutionError,
                                  ipmi._exec_ipmitool,
                                  self.info, 'A B C')
                mock_pwf.assert_called_once_with(self.info['password'])
                mock_exec.assert_called_once_with(*args, attempts=3)

    def test__power_status_on(self):
        with mock.patch.object(ipmi, '_exec_ipmitool',
                               autospec=True) as mock_exec:
            mock_exec.return_value = ["Chassis Power is on\n", None]

            state = ipmi._power_status(self.info)

            mock_exec.assert_called_once_with(self.info, "power status")
            self.assertEqual(states.POWER_ON, state)

    def test__power_status_off(self):
        with mock.patch.object(ipmi, '_exec_ipmitool',
                               autospec=True) as mock_exec:
            mock_exec.return_value = ["Chassis Power is off\n", None]

            state = ipmi._power_status(self.info)

            mock_exec.assert_called_once_with(self.info, "power status")
            self.assertEqual(states.POWER_OFF, state)

    def test__power_status_error(self):
        with mock.patch.object(ipmi, '_exec_ipmitool',
                               autospec=True) as mock_exec:
            mock_exec.return_value = ["Chassis Power is badstate\n", None]

            state = ipmi._power_status(self.info)

            mock_exec.assert_called_once_with(self.info, "power status")
            self.assertEqual(states.ERROR, state)

    def test__power_status_exception(self):
        with mock.patch.object(ipmi, '_exec_ipmitool',
                side_effect=processutils.ProcessExecutionError("error"),
                autospec=True) as mock_exec:
            self.assertRaises(exception.IPMIFailure,
                              ipmi._power_status,
                              self.info)
            mock_exec.assert_called_once_with(self.info, "power status")

    @mock.patch('eventlet.greenthread.sleep')
    def test__power_on_max_retries(self, sleep_mock):
        self.config(retry_timeout=2, group='ipmi')

        def side_effect(driver_info, command):
            resp_dict = {"power status": ["Chassis Power is off\n", None],
                         "power on": [None, None]}
            return resp_dict.get(command, ["Bad\n", None])

        with mock.patch.object(ipmi, '_exec_ipmitool',
                               autospec=True) as mock_exec:
            mock_exec.side_effect = side_effect

            expected = [mock.call(self.info, "power on"),
                        mock.call(self.info, "power status"),
                        mock.call(self.info, "power status")]

            state = ipmi._power_on(self.info)

            self.assertEqual(mock_exec.call_args_list, expected)
            self.assertEqual(states.ERROR, state)


class IPMIToolDriverTestCase(db_base.DbTestCase):

    def setUp(self):
        super(IPMIToolDriverTestCase, self).setUp()
        self.context = context.get_admin_context()
        self.dbapi = db_api.get_instance()
        mgr_utils.mock_the_extension_manager(driver="fake_ipmitool")
        self.driver = driver_factory.get_driver("fake_ipmitool")

        self.node = obj_utils.create_test_node(self.context,
                                               driver='fake_ipmitool',
                                               driver_info=INFO_DICT)
        self.info = ipmi._parse_driver_info(self.node)

    def test_get_power_state(self):
        returns = [["Chassis Power is off\n", None],
                   ["Chassis Power is on\n", None],
                   ["\n", None]]
        expected = [mock.call(self.info, "power status"),
                    mock.call(self.info, "power status"),
                    mock.call(self.info, "power status")]
        with mock.patch.object(ipmi, '_exec_ipmitool', side_effect=returns,
                               autospec=True) as mock_exec:

            pstate = self.driver.power.get_power_state(None, self.node)
            self.assertEqual(states.POWER_OFF, pstate)

            pstate = self.driver.power.get_power_state(None, self.node)
            self.assertEqual(states.POWER_ON, pstate)

            pstate = self.driver.power.get_power_state(None, self.node)
            self.assertEqual(states.ERROR, pstate)

            self.assertEqual(mock_exec.call_args_list, expected)

    def test_get_power_state_exception(self):
        with mock.patch.object(ipmi, '_exec_ipmitool',
                side_effect=processutils.ProcessExecutionError("error"),
                autospec=True) as mock_exec:
            self.assertRaises(exception.IPMIFailure,
                              self.driver.power.get_power_state,
                              None,
                              self.node)
        mock_exec.assert_called_once_with(self.info, "power status")

    def test_set_power_on_ok(self):
        self.config(retry_timeout=0, group='ipmi')

        with mock.patch.object(ipmi, '_power_on', autospec=True) as mock_on:
            mock_on.return_value = states.POWER_ON
            with mock.patch.object(ipmi, '_power_off',
                                   autospec=True) as mock_off:

                with task_manager.acquire(self.context,
                                         [self.node['uuid']]) as task:
                    self.driver.power.set_power_state(
                            task, self.node, states.POWER_ON)

                mock_on.assert_called_once_with(self.info)
                self.assertFalse(mock_off.called)

    def test_set_power_off_ok(self):
        self.config(retry_timeout=0, group='ipmi')

        with mock.patch.object(ipmi, '_power_on', autospec=True) as mock_on:
            with mock.patch.object(ipmi, '_power_off',
                                   autospec=True) as mock_off:
                mock_off.return_value = states.POWER_OFF

                with task_manager.acquire(self.context,
                                         [self.node['uuid']]) as task:
                    self.driver.power.set_power_state(
                            task, self.node, states.POWER_OFF)

                mock_off.assert_called_once_with(self.info)
                self.assertFalse(mock_on.called)

    def test_set_power_on_fail(self):
        self.config(retry_timeout=0, group='ipmi')

        with mock.patch.object(ipmi, '_power_on', autospec=True) as mock_on:
            mock_on.return_value = states.ERROR
            with mock.patch.object(ipmi, '_power_off',
                                   autospec=True) as mock_off:

                with task_manager.acquire(self.context,
                                         [self.node['uuid']]) as task:
                    self.assertRaises(exception.PowerStateFailure,
                            self.driver.power.set_power_state,
                            task,
                            self.node,
                            states.POWER_ON)

                mock_on.assert_called_once_with(self.info)
                self.assertFalse(mock_off.called)

    def test_set_power_invalid_state(self):
        with task_manager.acquire(self.context, [self.node['uuid']]) as task:
            self.assertRaises(exception.InvalidParameterValue,
                    self.driver.power.set_power_state,
                    task,
                    self.node,
                    "fake state")

    def test_set_boot_device_ok(self):
        with mock.patch.object(ipmi, '_exec_ipmitool',
                               autospec=True) as mock_exec:
            mock_exec.return_value = [None, None]

            with task_manager.acquire(self.context,
                                     [self.node['uuid']]) as task:
                self.driver.vendor._set_boot_device(task, 'pxe')

            mock_exec.assert_called_once_with(self.info, "chassis bootdev pxe")

    def test_set_boot_device_bad_device(self):
        with task_manager.acquire(self.context, [self.node['uuid']]) as task:
            self.assertRaises(exception.InvalidParameterValue,
                    self.driver.vendor._set_boot_device,
                    task,
                    'fake-device')

    def test_reboot_ok(self):
        manager = mock.MagicMock()
        #NOTE(rloo): if autospec is True, then manager.mock_calls is empty
        with mock.patch.object(ipmi, '_power_off', autospec=False) as mock_off:
            with mock.patch.object(ipmi, '_power_on',
                                   autospec=False) as mock_on:
                mock_on.return_value = states.POWER_ON
                manager.attach_mock(mock_off, 'power_off')
                manager.attach_mock(mock_on, 'power_on')
                expected = [mock.call.power_off(self.info),
                            mock.call.power_on(self.info)]

                with task_manager.acquire(self.context,
                                         [self.node['uuid']]) as task:
                    self.driver.power.reboot(task, self.node)

                self.assertEqual(manager.mock_calls, expected)

    def test_reboot_fail(self):
        manager = mock.MagicMock()
        #NOTE(rloo): if autospec is True, then manager.mock_calls is empty
        with mock.patch.object(ipmi, '_power_off', autospec=False) as mock_off:
            with mock.patch.object(ipmi, '_power_on',
                                   autospec=False) as mock_on:
                mock_on.return_value = states.ERROR
                manager.attach_mock(mock_off, 'power_off')
                manager.attach_mock(mock_on, 'power_on')
                expected = [mock.call.power_off(self.info),
                            mock.call.power_on(self.info)]

                with task_manager.acquire(self.context,
                                         [self.node['uuid']]) as task:
                    self.assertRaises(exception.PowerStateFailure,
                            self.driver.power.reboot,
                            task,
                            self.node)

                self.assertEqual(manager.mock_calls, expected)

    def test_vendor_passthru_validate__set_boot_device_good(self):
        with task_manager.acquire(self.context, self.node['uuid']) as task:
            self.driver.vendor.validate(task,
                                        method='set_boot_device',
                                        device='pxe')

    def test_vendor_passthru_validate__set_boot_device_fail(self):
        with task_manager.acquire(self.context, self.node['uuid']) as task:
            self.assertRaises(exception.InvalidParameterValue,
                              self.driver.vendor.validate,
                              task, method='set_boot_device',
                              device='fake')

    def test_vendor_passthru_validate__set_boot_device_fail_no_device(self):
        with task_manager.acquire(self.context, self.node['uuid']) as task:
            self.assertRaises(exception.InvalidParameterValue,
                              self.driver.vendor.validate,
                              task, method='set_boot_device')

    def test_vendor_passthru_validate_method_notmatch(self):
        with task_manager.acquire(self.context, self.node['uuid']) as task:
            self.assertRaises(exception.InvalidParameterValue,
                              self.driver.vendor.validate,
                              task, method='fake_method')

    def test_vendor_passthru_call_set_boot_device(self):
        with task_manager.acquire(self.context, [self.node['uuid']],
                                  shared=False) as task:
            with mock.patch.object(ipmi.VendorPassthru,
                                   '_set_boot_device') as boot_mock:
                self.driver.vendor.vendor_passthru(task,
                                                   method='set_boot_device',
                                                   device='pxe')
            boot_mock.assert_called_once_with(task, 'pxe', False)

    @mock.patch.object(ipmi, '_exec_ipmitool')
    def test_validate_ok(self, exec_mock):
        exec_mock.return_value = ('System GUID: fake', '')
        with task_manager.acquire(self.context,
                                  [self.node['uuid']]) as task:
            task.driver.power.validate(task, task.node)
            exec_mock.assert_called_once()

    @mock.patch.object(ipmi, '_exec_ipmitool')
    def test_validate_fail(self, exec_mock):
        exec_mock.side_effect = Exception
        with task_manager.acquire(self.context,
                                  [self.node['uuid']]) as task:
            self.assertRaises(exception.InvalidParameterValue,
                              task.driver.power.validate, task,
                              task.node)
            exec_mock.assert_called_once()

    @mock.patch.object(console_utils, 'start_shellinabox_console',
                       autospec=True)
    def test_start_console(self, mock_exec):
        mock_exec.return_value = None

        with task_manager.acquire(self.context,
                                  [self.node['uuid']]) as task:
            self.driver.console.start_console(task, self.node)

        mock_exec.assert_called_once_with(self.info['uuid'],
                                          self.info['port'],
                                          mock.ANY)
        self.assertTrue(mock_exec.called)

    @mock.patch.object(console_utils, 'start_shellinabox_console',
                       autospec=True)
    def test_start_console_fail(self, mock_exec):
        mock_exec.side_effect = exception.ConsoleSubprocessFailed(
                error='error')

        with task_manager.acquire(self.context,
                                  [self.node['uuid']]) as task:
            self.assertRaises(exception.ConsoleSubprocessFailed,
                              self.driver.console.start_console,
                              task, self.node)

    @mock.patch.object(console_utils, 'stop_shellinabox_console',
                       autospec=True)
    def test_stop_console(self, mock_exec):
        mock_exec.return_value = None

        with task_manager.acquire(self.context,
                                  [self.node['uuid']]) as task:
            self.driver.console.stop_console(task, self.node)

        mock_exec.assert_called_once_with(self.info['uuid'])
        self.assertTrue(mock_exec.called)

    @mock.patch.object(console_utils, 'get_shellinabox_console_url',
                       utospec=True)
    def test_get_console(self, mock_exec):
        url = 'http://localhost:4201'
        mock_exec.return_value = url
        expected = {'type': 'shellinabox', 'url': url}

        with task_manager.acquire(self.context,
                                  [self.node['uuid']]) as task:
            console_info = self.driver.console.get_console(task,
                                                           self.node)

        self.assertEqual(expected, console_info)
        mock_exec.assert_called_once_with(self.info['port'])
        self.assertTrue(mock_exec.called)
