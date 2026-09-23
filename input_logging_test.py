"""Input privacy applies below the authenticated packet handler as well."""
import logging
import unittest
from input_xpra import private_logging


class PrivateLoggingTest(unittest.TestCase):
    def test_packet_diagnostics_never_forward_body_or_exception(self):
        received = []
        emit = private_logging(lambda *args, **kwargs: received.append((args, kwargs)))
        for category in ('network.protocol', 'network.crypto', 'clipboard', 'keyboard'):
            callback = logging.getLogger('xpra.fixture.' + category).log
            emit(callback, 10, 'private text', 'private argument', exc_info=ValueError('private error'))
            self.assertEqual(received, [])
            emit(callback, 40, 'private text', 'private argument', exc_info=ValueError('private error'))
            self.assertEqual(received.pop(), ((callback, 40, 'Private input transport diagnostic; details withheld'), {}))

    def test_unrelated_startup_diagnostics_remain_available(self):
        received = []
        emit = private_logging(lambda *args, **kwargs: received.append((args, kwargs)))
        callback = logging.getLogger('xpra.server.display').log
        emit(callback, 40, 'Display unavailable: %s', 'display-1')
        self.assertEqual(received, [((callback, 40, 'Display unavailable: %s', 'display-1'), {})])


if __name__ == '__main__':
    unittest.main()
