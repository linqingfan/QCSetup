from AlgorithmImports import *
from QuantConnect.DataSource import *

class ForexCarryTradeAlgorithm(QCAlgorithm):

    def initialize(self) -> None:
        
        self.SetStartDate(2008, 1, 14)  # Adjust to match data in your CSV
        self.SetEndDate(2008, 1, 15)    # Adjust to match data in your CSV
        self.set_cash(10000)
        #self.set_time_zone("America/New_York")
        self.set_time_zone("UTC")
        # We will use hard-coded interest rates for each base currency
        self.rates = {
            "USDAUD": 1.5,    # Australia
            "USDCAD": 0.5,    # Canada
            "USDCNY": 4.35,   # China
            "USDEUR": 0.0,    # Euro Area
            "USDINR": 6.5,    # India
            "USDJPY": -0.1,   # Japan
            "USDMXN": 4.25,   # Mexico
            "USDTRY": 7.5,    # Turkey
            "USDZAR": 7.0     # South Africa
        }

        # Subscribe to forex data for trading
        fx=self.add_forex("EURUSD", Resolution.MINUTE, Market.OANDA)
        self.custom_symbol = fx.Symbol
        self.cnt = 0
        self.first_bar=True

    def OnData(self, data: Slice):
        # Check if our custom data is in the slice
        if not data.ContainsKey(self.custom_symbol) or data[self.custom_symbol] is None:
            # self.Log(f"No custom data for {self.custom_symbol} at {self.Time}")
            return

        custom_bar = data[self.custom_symbol] # This is an instance of IbkrEURUSDData

        if self.cnt <5:
            self.Debug(f"First custom bar received at {self.Time}:")
            self.Debug(f"  Time: {custom_bar.time}")
            self.Debug(f"  Symbol: {custom_bar.Symbol}")
            self.Debug(f"  Value (Mid Close): {custom_bar.value:.5f}")
            self.Debug(f"  Bid Open: {custom_bar.Bid.Open:.5f}, Bid High: {custom_bar.Bid.High:.5f}, Bid Low: {custom_bar.Bid.Low:.5f}, Bid Close: {custom_bar.Bid.Close:.5f}, Bid Vol: {custom_bar.LastBidSize}")
            self.Debug(f"  Ask Open: {custom_bar.Ask.Open:.5f}, Ask High: {custom_bar.Ask.High:.5f}, Ask Low: {custom_bar.Ask.Low:.5f}, Ask Close: {custom_bar.Ask.Close:.5f}, Ask Vol: {custom_bar.LastAskSize}")
            self.Debug(f"  Standard Open (Mid): {custom_bar.Open:.5f}, High: {custom_bar.High:.5f}, Low: {custom_bar.Low:.5f}, Close: {custom_bar.Close:.5f}")
            self.first_bar = False
        self.cnt +=1

        if not self.Portfolio.Invested:
            self.SetHoldings(self.custom_symbol, 0.5)
