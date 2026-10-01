"""Map fixture-owned DOM geometry through the actual captured Firefox window."""
import ast
from pathlib import Path
import unittest


class FirefoxGeometryTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).with_name('firefox_session_probe.py')
        tree = ast.parse(source.read_text(), filename=str(source))
        definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'fixture_point')
        namespace = {}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), str(source), 'exec'), namespace)
        self.point = namespace['fixture_point']
        self.receipt = {'save_target': {'x': 82.5, 'y': 570, 'width': 1000, 'height': 700}}

    def test_current_firefox_button_is_not_the_retired_fixed_coordinate(self):
        self.assertEqual(self.point(self.receipt, {'width': 1000, 'height': 700}), (82, 570))

    def test_capture_density_scales_fixture_coordinates(self):
        self.assertEqual(self.point(self.receipt, {'width': 2000, 'height': 1400}), (165, 1140))

    def test_off_window_and_invalid_geometry_cannot_authorize_a_click(self):
        for field, value in [('width', 0), ('height', 0), ('x', 1000), ('y', -1), ('x', float('nan'))]:
            with self.subTest(field=field, value=value):
                receipt = {'save_target': {**self.receipt['save_target'], field: value}}
                with self.assertRaises(ValueError):
                    self.point(receipt, {'width': 1000, 'height': 700})


if __name__ == '__main__':
    unittest.main()
