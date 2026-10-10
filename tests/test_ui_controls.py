import unittest

from brio_control.ui_controls import control_info, option_label, sort_controls


class UIControlTests(unittest.TestCase):
    def test_known_ids_are_translated_without_requiring_kind(self):
        info = control_info({"id": "0X9A0901", "name": "driver name"})
        self.assertEqual(info, {"title": "Exposição automática", "group": "exposure"})
        self.assertEqual(control_info({"id": "0x9a090a", "kind": "menu"})["group"], "focus")
        self.assertEqual(control_info({"id": "fov"}, portuguese=False),
                         {"title": "Field of view", "group": "native_framing"})

    def test_unknown_ids_keep_driver_name(self):
        self.assertEqual(control_info({"id": "0xdeadbeef", "name": "Driver label"}),
                         {"title": "Driver label", "group": "advanced"})

    def test_known_menu_options_and_unknown_driver_labels(self):
        control = {"id": "0x980918"}
        self.assertEqual(option_label(control, {"value": 2, "label": "60 Hz"}), "60 Hz")
        self.assertEqual(option_label(control, {"value": 1, "label": "50 Hz"}, False), "50 Hz")
        self.assertEqual(option_label({"id": "0xdead"}, {"value": 7, "label": "Driver option"}),
                         "Driver option")

    def test_ordering_places_automatic_controls_before_manual_controls(self):
        pairs = [
            ("0x9a0901", "0x9a0902"),
            ("0x98090c", "0x98091a"),
            ("0x9a090c", "0x9a090a"),
        ]
        for automatic, manual in pairs:
            with self.subTest(automatic=automatic):
                ordered = sort_controls([{"id": manual}, {"id": automatic}])
                self.assertEqual([item["id"] for item in ordered], [automatic, manual])

    def test_exposure_auto_menu_option_precedes_manual_semantics(self):
        control = {"id": "0x9a0901"}
        self.assertEqual(option_label(control, {"value": 0, "label": "Auto"}), "Automática")
        self.assertEqual(option_label(control, {"value": 1, "label": "Manual"}), "Manual")


if __name__ == "__main__":
    unittest.main()
