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
ticker = "1211"
currency = "HKD"
market = "hkfe"
tz = "Asia/Hong_Kong"

#ticker = "JD"
#currency = "USD"
#market = "usa"
#tz = "America/New_York"

class TradingAlgorithm(QCAlgorithm):

    def initialize(self):
        # Locally Lean installs free sample data, to download more data please visit https://www.quantconnect.com/docs/v2/lean-cli/datasets/downloading-data
        self.set_start_date(2025, 1, 14)  # Set Start Date
        self.set_end_date(2025, 1, 16)  # Set End Date
        #self.set_start_date(2019, 7, 9)  # Set Start Date
        #self.set_end_date(2019, 7, 11)  # Set End Date
        self.set_time_zone(tz)
        
        self.set_account_currency(currency)
        self.set_cash(currency,10000)  # Set Strategy Cash

        # --- Register the custom market (if not already done) ---
        #Market.Add(market, 100) # Use a unique ID if 100 is taken

        # --- Add the equity using the generated hours ---
        self.asset=self.AddEquity(ticker=ticker,
                    resolution=Resolution.Minute,
                    market=market,
                    #fillForward=False,  # Set this to False
                    #leverage=1.0, # Adjust as needed
                    #extendedMarketHours=False, # Adjust based on data/strategy
                    #dataNormalizationMode=DataNormalizationMode.Raw
                    ) # Adjust as needed
        self.symbol = self.asset.Symbol
        self.Debug("Initialize method completed.****************") # <-- ADDED DEBUG

    def on_data(self, data: Slice):
        """on_data event is the primary entry point for your algorithm. Each new data point will be pumped in here.
            Arguments:
                data: Slice object keyed by symbol containing the stock data
        """
        self.Debug(f"OnData called at {self.Time}===================") # <-- ADDED DEBUG

        if data is None or data.Count == 0:
            self.Log(f"Time: {self.Time} - Slice is None or empty. Skipping.")
            return

        # 2. Warm-up check
        if self.IsWarmingUp:
            self.Log(f"Time: {self.Time} - Algorithm is warming up. Indicator IsReady: {self.sma_spy.IsReady}. Skipping logic.")
            # You can still access data during warm-up, e.g., to prime indicators:
            # if data.Bars.ContainsKey(self.spy):
            #     self.Log(f"Warming up with SPY bar: {data.Bars[self.spy].Close}")
            return

        if not data.ContainsKey(self.symbol):
            self.Log(f"Time: {self.Time} - No data for {self.symbol} in current slice. Keys: {', '.join([str(k) for k in data.Keys]) if data.Keys else 'None'}")
            return

        # At this point, we know data.Bars contains self.spy
        bar = data.Bars[self.symbol] 
        # Print the OHLC, Volume, and fill-forward status
        self.Debug(f"Time: {self.Time} | Ticker: {ticker} | O: {bar.Open} | H: {bar.High} | L: {bar.Low} | C: {bar.Close} | Vol: {bar.Volume}")
        self.Debug(f"  Bar Details -> IsFillForward: {bar.IsFillForward}, Price: {bar.Price}")

        # --- Trading Logic ---
        # Only attempt to trade if we have data for the symbol AND are not invested
        if not self.portfolio.invested:
            self.set_holdings(ticker, 1)
            self.debug("Purchased Stock")
