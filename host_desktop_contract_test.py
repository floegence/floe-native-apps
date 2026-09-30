"""Authority and backpressure scenarios for a shared physical desktop."""
import unittest

from host_desktop_contract import DesktopAuthority, FrameCredit, DesktopError, capture_size


class DesktopAuthorityTests(unittest.TestCase):
    def test_a_viewer_must_paint_the_current_display_before_controlling_it(self):
        authority = DesktopAuthority()
        generation = authority.bind('display-a', 'control')
        with self.assertRaises(DesktopError):
            authority.input(generation)
        authority.sent(1)
        authority.paint(generation, 1)
        authority.input(generation)
        replacement = authority.bind('display-b', 'control')
        with self.assertRaises(DesktopError):
            authority.paint(generation, 1)
        with self.assertRaises(DesktopError):
            authority.input(replacement)

    def test_view_only_and_revocation_reject_input_despite_valid_pixels(self):
        authority = DesktopAuthority()
        generation = authority.bind('display-a', 'view')
        authority.sent(1)
        authority.paint(generation, 1)
        with self.assertRaises(DesktopError):
            authority.input(generation)
        authority.mode = 'control'
        authority.input(generation)
        authority.revoke('locked')
        with self.assertRaises(DesktopError):
            authority.input(generation)

    def test_acknowledgement_cannot_claim_unsent_pixels_or_regress(self):
        authority = DesktopAuthority()
        generation = authority.bind('display-a', 'control')
        authority.sent(1)
        with self.assertRaises(DesktopError):
            authority.paint(generation, 2)
        authority.paint(generation, 1)
        with self.assertRaises(DesktopError):
            authority.paint(generation, 0)

    def test_invalid_binding_does_not_retire_an_active_display(self):
        authority = DesktopAuthority()
        generation = authority.bind('display-a', 'control')
        with self.assertRaises(DesktopError):
            authority.bind('', 'control')
        self.assertEqual(authority.generation, generation)
        self.assertEqual(authority.display, 'display-a')


class FrameCreditTests(unittest.TestCase):
    def test_pipeline_encodes_several_frames_without_a_network_round_trip(self):
        credit = FrameCredit()
        frames = [credit.reserve() for _ in range(4)]
        self.assertEqual(frames, [1, 2, 3, 4])
        self.assertIsNone(credit.reserve())
        credit.acknowledge(2)
        self.assertEqual(credit.reserve(), 5)
        self.assertEqual(credit.reserve(), 6)
        self.assertIsNone(credit.reserve())

    def test_unsent_acknowledgement_does_not_grant_more_credit(self):
        credit = FrameCredit()
        credit.reserve()
        with self.assertRaises(DesktopError):
            credit.acknowledge(100)
        self.assertEqual(credit.pending, 1)

    def test_retired_generation_cannot_reuse_frame_credit(self):
        credit = FrameCredit()
        first = credit.reserve()
        credit.reset()
        current = credit.reserve()
        self.assertGreater(current, first)
        credit.acknowledge(first)
        self.assertEqual(credit.pending, 1)

    def test_pixel_limit_never_invents_source_detail(self):
        self.assertEqual(capture_size(1920, 1080, 2560), (1920, 1080))
        self.assertEqual(capture_size(4608, 2592, 2560, native=True), (4608, 2592))
        with self.assertRaises(DesktopError):
            capture_size(4609, 2592, 2560, native=True)
        self.assertEqual(capture_size(3840, 2160, 2560), (2560, 1440))
        self.assertEqual(capture_size(1080, 1920, 1600), (900, 1600))
        for args in ((0, 1080, 2560), (1920, 1080, 0), (1920, 1080, float('nan'))):
            with self.assertRaises(DesktopError):
                capture_size(*args)


if __name__ == '__main__':
    unittest.main()
