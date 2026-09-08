import unittest


class RadarStrictJsonTests(unittest.TestCase):
    def test_accepts_utf8_bytes_and_text_without_duplicate_keys(self):
        from radar.strict_json import strict_json_loads

        expected = {"outer": {"value": 0}, "items": [True, None, "中文"]}
        self.assertEqual(
            strict_json_loads(
                b'{"outer":{"value":0},"items":[true,null,"\xe4\xb8\xad\xe6\x96\x87"]}'
            ),
            expected,
        )
        self.assertEqual(strict_json_loads('{"value":0}'), {"value": 0})

    def test_rejects_duplicate_keys_at_every_depth(self):
        from radar.strict_json import strict_json_loads

        for payload in (
            '{"value":1,"value":1}',
            '{"outer":{"value":1,"value":2}}',
        ):
            with self.subTest(payload=payload), self.assertRaisesRegex(
                ValueError, "radar_json_unverified"
            ):
                strict_json_loads(payload)

    def test_rejects_non_finite_constants_and_invalid_utf8(self):
        from radar.strict_json import strict_json_loads

        for payload in ('{"value":NaN}', '{"value":Infinity}', '{"value":-Infinity}'):
            with self.subTest(payload=payload), self.assertRaisesRegex(
                ValueError, "radar_json_unverified"
            ):
                strict_json_loads(payload)
        with self.assertRaisesRegex(ValueError, "radar_json_unverified"):
            strict_json_loads(b'{"value":"\xff"}')

    def test_supports_stable_caller_specific_error_code(self):
        from radar.strict_json import strict_json_loads

        with self.assertRaisesRegex(ValueError, "checkpoint_pointer_unverified"):
            strict_json_loads(
                '{"value":1,"value":2}',
                error_code="checkpoint_pointer_unverified",
            )


if __name__ == "__main__":
    unittest.main()
