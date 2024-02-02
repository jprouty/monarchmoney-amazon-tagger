import unittest

from monarchmoneyamazontagger.micro_usd import MicroUSD


class MicroUSDTest(unittest.TestCase):
    def test_equality(self):
        self.assertEqual(MicroUSD(0), MicroUSD(-10))
        self.assertEqual(MicroUSD(0), MicroUSD(10))
        self.assertEqual(MicroUSD(-10), MicroUSD(0))
        self.assertEqual(MicroUSD(10), MicroUSD(0))
        self.assertEqual(MicroUSD(10), MicroUSD(-10))
        self.assertEqual(MicroUSD(-10), MicroUSD(10))

        self.assertEqual(MicroUSD(42143241), MicroUSD(42143239))
        self.assertEqual(MicroUSD(42143241), MicroUSD(42143243))

        self.assertNotEqual(MicroUSD(0), MicroUSD(400))
        self.assertNotEqual(MicroUSD(0), MicroUSD(-200))
        self.assertNotEqual(MicroUSD(-500), MicroUSD(0))
        self.assertNotEqual(MicroUSD(200), MicroUSD(0))

    def test_to_float(self):
        self.assertEqual(MicroUSD(30000300).to_float(), 30.0)
        self.assertEqual(MicroUSD(103000).to_float(), 0.10)
        self.assertEqual(MicroUSD(303103000).to_float(), 303.10)
        self.assertEqual(MicroUSD(-103100000).to_float(), -103.10)
        self.assertEqual(MicroUSD(5050500).to_float(), 5.05)
        self.assertEqual(MicroUSD(150500).to_float(), 0.15)
        self.assertEqual(MicroUSD(500).to_float(), 0)
        self.assertEqual(MicroUSD(-500).to_float(), 0)

    def test_round_to_cent(self):
        self.assertEqual(MicroUSD(50505050).round_to_cent().micro_usd, 50510000)
        self.assertEqual(MicroUSD(50514550).round_to_cent().micro_usd, 50510000)
        self.assertEqual(MicroUSD(-550).round_to_cent().micro_usd, 0)
        self.assertEqual(MicroUSD(550).round_to_cent().micro_usd, 0)
        self.assertEqual(MicroUSD(-66130000).round_to_cent().micro_usd, -66130000)
        self.assertEqual(MicroUSD(32940000).round_to_cent().micro_usd, 32940000)
        self.assertEqual(MicroUSD(-32870000).round_to_cent().micro_usd, -32870000)
        self.assertEqual(MicroUSD(-33120000).round_to_cent().micro_usd, -33120000)
        self.assertEqual(MicroUSD(-67070000).round_to_cent().micro_usd, -67070000)

    def test_from_float(self):
        self.assertEqual(MicroUSD.from_float(32.94).micro_usd, 32940000)

    def test_str(self):
        self.assertEqual(str(MicroUSD(1230040)), "$1.23")
        self.assertEqual(str(MicroUSD(-123000)), "-$0.12")
        self.assertEqual(str(MicroUSD(-1900)), "$0.00")
        self.assertEqual(str(MicroUSD(-10000)), "-$0.01")

    def test_parse(self):
        self.assertEqual(MicroUSD.parse("$1.23").micro_usd, 1230000)
        self.assertEqual(MicroUSD.parse("$0.00").micro_usd, 0)
        self.assertEqual(MicroUSD.parse("-$0.00").micro_usd, 0)
        self.assertEqual(MicroUSD.parse("$55").micro_usd, 55000000)
        self.assertEqual(MicroUSD.parse("$12.23").micro_usd, 12230000)
        self.assertEqual(MicroUSD.parse("-$12.23").micro_usd, -12230000)


if __name__ == "__main__":
    unittest.main()
