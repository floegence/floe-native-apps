"""Clipboard publication shares native input ordering and never commits text."""
from types import SimpleNamespace
import unittest

from desktop_clipboard import NativeClipboard
from input_order import OrderedInput


class ClipboardTests(unittest.TestCase):
    def setUp(self):
        self.sent, self.results, self.changed = [], [], []
        self.target = SimpleNamespace(window=3, generation=7)
        self.native = SimpleNamespace(epoch=1, closed=False, target=self.target,
                                      send=self.sent.append,
                                      submit=lambda epoch, target, commands: self.sent.extend(commands))
        self.clipboard = NativeClipboard(self.native, self.changed.append)

    def test_publication_waits_for_native_selection_and_keeps_unicode(self):
        token = self.clipboard.token(self.target)
        self.clipboard.commit(token, '你好 e\u0301 👨‍👩‍👧‍👦', self.results.append)
        self.assertEqual(self.results, [])
        self.assertTrue(self.sent[0].startswith('clipboard-set 1 '))
        self.assertEqual(bytes.fromhex(self.sent[0].split()[2]).decode(), '你好 e\u0301 👨‍👩‍👧‍👦')
        self.clipboard.observe(['clipboard-published', '1'])
        self.assertEqual(self.results, [None])

    def test_empty_selection_is_distinct_from_a_text_commit(self):
        token = self.clipboard.token(self.target)
        self.clipboard.commit(token, '', self.results.append)
        self.assertEqual(self.sent, ['clipboard-set 1 -'])

    def test_cancel_and_new_target_reject_a_late_publication(self):
        first = self.clipboard.token(self.target)
        self.clipboard.commit(first, 'old', self.results.append)
        self.clipboard.cancel(first)
        self.native.target = SimpleNamespace(window=3, generation=8)
        next_token = self.clipboard.token(self.native.target)
        self.clipboard.commit(next_token, 'new', self.results.append)
        self.clipboard.observe(['clipboard-published', '1'])
        self.assertEqual(self.results, [])
        self.clipboard.observe(['clipboard-published', '2'])
        self.assertEqual(self.results, [None])

    def test_target_replacement_before_acknowledgement_fails(self):
        self.clipboard.commit(self.clipboard.token(self.target), 'old', self.results.append)
        self.native.target = SimpleNamespace(window=3, generation=7)
        self.clipboard.observe(['clipboard-published', '1'])
        self.assertEqual(self.results, ['INPUT_TARGET_UNAVAILABLE'])

    def test_read_chunks_are_bounded_and_never_cross_connection(self):
        self.clipboard.observe(['clipboard-state', '1', '1', '7', '3', '6'])
        self.assertEqual(self.sent, ['clipboard-read 1 0\n'])
        self.native.epoch = 2
        self.clipboard.observe(['clipboard-data', '1', '0', '你好'.encode().hex()])
        self.assertEqual(self.changed, [])

    def test_selection_bytes_are_delivered_once_after_complete_utf8(self):
        value = '你好' * 200
        data = value.encode()
        self.clipboard.observe(['clipboard-state', '1', '1', '7', '3', str(len(data))])
        self.clipboard.observe(['clipboard-data', '1', '0', data[:1024].hex()])
        self.assertEqual(self.changed, [])
        self.clipboard.observe(['clipboard-data', '1', '1024', data[1024:].hex()])
        self.assertEqual(self.changed, [(self.target, 1, value)])

    def test_superseding_selection_does_not_accumulate_old_reads(self):
        self.clipboard.observe(['clipboard-state', '1', '1', '7', '3', '3'])
        self.clipboard.observe(['clipboard-state', '2', '1', '7', '3', '3'])
        self.assertEqual(len(self.sent), 1)
        self.clipboard.observe(['clipboard-data', '1', '0', '-'])
        self.assertEqual(self.sent[-1], 'clipboard-read 2 0\n')
        self.clipboard.observe(['clipboard-data', '2', '0', b'new'.hex()])
        self.assertEqual(self.changed, [(self.target, 1, 'new')])

    def test_invalid_or_oversized_text_does_not_enter_native_transport(self):
        for value in ('\x00', '\ud800', 'a' * 16001, None):
            with self.subTest(value=type(value).__name__):
                self.clipboard.commit(self.clipboard.token(self.target), value, self.results.append)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.results, ['CLIPBOARD_TEXT_INVALID'] * 4)

    def test_withdrawn_current_selection_reports_unavailable(self):
        self.clipboard.observe(['clipboard-state', '1', '1', '7', '3', '3'])
        self.clipboard.observe(['clipboard-data', '1', '0', '-'])
        self.assertEqual(self.changed, [(self.target, 1, None)])

    def test_empty_unavailable_and_invalid_utf8_remain_distinct(self):
        self.clipboard.observe(['clipboard-state', '1', '1', '7', '3', '0'])
        self.clipboard.observe(['clipboard-state', '2', '1', '7', '3', '-1'])
        self.clipboard.observe(['clipboard-state', '3', '1', '7', '3', '1'])
        self.clipboard.observe(['clipboard-data', '3', '0', 'ff'])
        self.assertEqual(self.changed, [(self.target, 1, ''), (self.target, 1, None), (self.target, 1, None)])

    def test_invalidation_discards_old_chunks_and_allows_the_next_revision(self):
        self.clipboard.observe(['clipboard-state', '1', '1', '7', '3', '3'])
        self.clipboard.invalidate()
        self.clipboard.observe(['clipboard-state', '2', '1', '7', '3', '3'])
        self.assertEqual(len(self.sent), 1)
        self.clipboard.observe(['clipboard-data', '1', '0', b'old'.hex()])
        self.clipboard.observe(['clipboard-data', '2', '0', b'new'.hex()])
        self.assertEqual(self.changed, [(self.target, 1, 'new')])

    def test_close_fails_pending_publication_once_and_discards_late_records(self):
        self.clipboard.commit(self.clipboard.token(self.target), 'pending', self.results.append)
        self.clipboard.observe(['clipboard-state', '1', '1', '7', '3', '3'])
        self.clipboard.close()
        self.clipboard.close()
        self.clipboard.observe(['clipboard-published', '1'])
        self.clipboard.observe(['clipboard-data', '1', '0', b'old'.hex()])
        self.assertEqual(self.results, ['CLIPBOARD_UNAVAILABLE'])
        self.assertEqual(self.changed, [])

    def test_short_chunk_and_unsolicited_data_fail_without_content_in_errors(self):
        self.clipboard.observe(['clipboard-state', '1', '1', '7', '3', '6'])
        with self.assertRaisesRegex(ValueError, '^Invalid native clipboard chunk extent$'):
            self.clipboard.observe(['clipboard-data', '1', '0', b'abc'.hex()])
        with self.assertRaisesRegex(ValueError, '^Unsolicited native clipboard data$'):
            self.clipboard.observe(['clipboard-data', '2', '0', b'abc'.hex()])

    def test_following_paste_keys_wait_on_the_existing_input_scheduler(self):
        delivered = []
        owner = object()
        def admit(_owner, operation):
            if operation == 'clipboard':
                return 1, self.clipboard, self.clipboard.token(self.target), 'paste'
            delivered.append(operation)
        order = OrderedInput(lambda candidate: candidate is owner, admit,
                             lambda *_: None, lambda *_: self.fail('Unexpected overflow'),
                             lambda *_: 1, lambda *_: None)
        order.enqueue(owner, 'clipboard')
        order.enqueue(owner, 'paste-key')
        self.assertEqual(delivered, [])
        self.clipboard.observe(['clipboard-published', '1'])
        self.assertEqual(delivered, ['paste-key'])
        order.close()


if __name__ == '__main__':
    unittest.main()
