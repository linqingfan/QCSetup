# region imports
from AlgorithmImports import *
from QuantConnect import DataNormalizationMode
from QuantConnect.Securities import SecurityExchangeHours, LocalMarketHours 
from QuantConnect import TimeZones, Resolution
from QuantConnect import Resolution
from datetime import datetime, date, timedelta
from System import TimeSpan, DateTime # Import .NET DateTime
from System.Collections.Generic import Dictionary, List # Import .NET Dictionary and List types
# endregion

#ticker = "1211"
#currency = "HKD"
#market = "hkfe"
#tz = "Asia/Hong_Kong"

ticker = "NVDA"
currency = "USD"
market = "usa"
tz = "America/New_York"

class TradingAlgorithm(QCAlgorithm):

    def initialize(self):
        self.set_start_date(2025, 1, 14)  # Set Start Date
        self.set_end_date(2025, 1, 16)  # Set End Date
        self.set_time_zone(tz)
        
        self.set_account_currency(currency)
        self.set_cash(currency,10000)  # Set Strategy Cash

        # --- Register the custom market (if not already done) ---
        #Market.Add(market, 100) # Use a unique ID if 100 is taken

        # --- Add the equity using the generated hours ---
        self.asset=self.AddEquity(ticker=ticker,
                    resolution=Resolution.Minute,
                    market=market,
                    #fillForward=False,
                    leverage=1.0
                    ) # Adjust as needed
        self.symbol = self.asset.Symbol
        self.Debug("Initialize method completed.****************")

    def on_data(self, data: Slice):
        """on_data event is the primary entry point for your algorithm. Each new data point will be pumped in here.
            Arguments:
                data: Slice object keyed by symbol containing the stock data
        """
        self.Debug(f"OnData called at {self.Time}===================")
        if data is None or data.Count == 0:
            self.Log(f"Time: {self.Time} - Slice is None or empty. Skipping.")
            return
        if not data.ContainsKey(self.symbol):
            self.Log(f"Time: {self.Time} - No data for {self.symbol} in current slice. Keys: {', '.join([str(k) for k in data.Keys]) if data.Keys else 'None'}")
            return
        bar = data.Bars[self.symbol] 
        # Print the OHLC, Volume, and fill-forward status
        self.Debug(f"Time: {self.Time} | Ticker: {ticker} | O: {bar.Open} | H: {bar.High} | L: {bar.Low} | C: {bar.Close} | Vol: {bar.Volume}")
        self.Debug(f"  Bar Details -> IsFillForward: {bar.IsFillForward}, Price: {bar.Price}")

        # --- Trading Logic ---
        # Only attempt to trade if we have data for the symbol AND are not invested
        if not self.portfolio.invested:
            self.set_holdings(ticker, 1)
            self.debug("Purchased Stock")
