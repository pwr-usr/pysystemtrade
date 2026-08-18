from systems.tests.testdata import get_test_object
from systems.basesystem import System
import pandas as pd
import unittest
from unittest.mock import patch


class Test(unittest.TestCase):
    def setUp(self):
        (rawdata, data, config) = get_test_object()

        system = System([rawdata], data)
        self.system = system

    @unittest.SkipTest
    def test_daily_denominator_price(self):
        self.assertAlmostEqual(
            self.system.rawdata.daily_denominator_price("SOFR").tail(1).values[0],
            97.4425,
            places=4,
        )

    @unittest.SkipTest
    def test_daily_returns(self):
        self.assertAlmostEqual(
            self.system.rawdata.daily_returns("SOFR").tail(1).values[0], -0.0225
        )

    def test_daily_returns_across_closed_business_weekdays(self):
        dates = pd.bdate_range("2024-09-30", periods=4)
        prices = pd.Series([100.0, float("nan"), float("nan"), 106.0], index=dates)

        with patch.object(self.system.rawdata, "get_daily_prices", return_value=prices):
            returns = self.system.rawdata.daily_returns("SYNTHETIC_CLOSURE")

        self.assertEqual(returns.loc[dates[1]], 0.0)
        self.assertEqual(returns.loc[dates[2]], 0.0)
        self.assertEqual(returns.loc[dates[3]], 6.0)

    @unittest.SkipTest
    def test_daily_returns_volatility(self):
        self.assertAlmostEqual(
            self.system.rawdata.daily_returns_volatility("SOFR").tail(1).values[0],
            0.03327772,
            places=6,
        )

    @unittest.SkipTest
    def test_daily_percentage_volatility(self):
        self.assertAlmostEqual(
            self.system.rawdata.get_daily_percentage_volatility("SOFR")
            .tail(1)
            .values[0],
            0.034143,
            places=6,
        )

    @unittest.SkipTest
    def test_norm_returns(self):
        self.assertAlmostEqual(
            self.system.rawdata.get_daily_vol_normalised_returns("SOFR")
            .tail(1)
            .values[0],
            -0.67556593,
            places=6,
        )


if __name__ == "__main__":
    unittest.main()
