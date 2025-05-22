# QuantConnect Local Lean Setup

This guide provides instructions to set up QuantConnect Lean to run locally on Windows without using Docker. It assumes you have some familiarity with Visual Studio Code (VS Code).

## Install the Latest .NET
Download and install the latest version of .NET (e.g., .NET 9.0 or newer) from the official website:
[https://dotnet.microsoft.com/en-us/download/dotnet/9.0](https://dotnet.microsoft.com/en-us/download/dotnet/9.0)

## Download QuantConnect LEAN Source Codes
Clone the QuantConnect Lean repository using GitHub Desktop or the command line. Ensure Git is installed on your system:
```bash
git clone https://github.com/QuantConnect/Lean
```

## Fix `debugpy` Bugs on Windows Platform (Non-Docker)
On Windows, the `debugpy` module in QuantConnect may fail to locate the Python executable. This quick fix is specific to Windows and may not be needed on Linux or other platforms.

Modify `AlgorithmFactory/DebuggerHelper.cs` by adding the following lines after `import debugpy`:
```csharp
// ... existing C# code ...
// In the section where Python commands are prepared for debugpy:
// Ensure the python executable path is correctly resolved for the venv.
// The original code might look something like:
// process.StartInfo.ArgumentList.Add("-m");
// process.StartInfo.ArgumentList.Add("debugpy");
// process.StartInfo.ArgumentList.Add("--listen");
// process.StartInfo.ArgumentList.Add($"{config.Host}:{config.Port}");

// You might need to adjust how the Python executable is called if it's not found.
// A more robust way is to ensure your `PATH` environment variable within the
// context Lean runs in includes the venv's Scripts directory, or to explicitly
// provide the full path to the Python executable from the venv.
// The C# modification provided in the original instructions:
// ```python
// import debugpy
// import os, sys
// python_executable_in_venv = os.path.join(sys.prefix, 'Scripts', 'python.exe')
// debugpy.configure(python=python_executable_in_venv)
// ```
// is Python code meant to be executed by the Python interpreter that debugpy launches.
// The actual C# change would involve how `DebuggerHelper.cs` constructs the command
// to launch Python with debugpy, potentially by setting the Python executable path
// explicitly if the default resolution fails.
// For example, if `pythonPath` is a variable in C# holding the path to python.exe:
// process.StartInfo.FileName = pythonPath; // Instead of just "python"
```
*(Self-correction: The original `debugpy` fix was Python code. The actual C# fix would involve how `DebuggerHelper.cs` finds/calls the Python executable. This section needs more context on the C# side or a confirmation that the Python snippet is injected/run appropriately by Lean's C# debugger setup.)*

## Set Up a Python Virtual Environment
Set the environment variable **`PYTHONNET_PYDLL`** to the path of your system Python DLL, e.g., `C:\Users\username\AppData\Local\Programs\Python\Python311\python311.dll`.

In the QuantConnect Lean root folder (assuming Python 3.11 is installed), run these commands:
```bash
python -m venv .venv
.venv\Scripts\activate
python.exe -m pip install --upgrade pip
pip install wheel jupyterlab pandas==2.1.4 wrapt==1.16.0 clr_loader==0.1.6 matplotlib debugpy
python -m ipykernel install --user --name="lean_venv_kernel" --display-name="Python (Lean Venv)"
pip install --no-cache-dir quantconnect-stubs
```
Afterward, in VS Code, select the `.venv` environment as your Python interpreter (Ctrl+Shift+P, then "Python: Select Interpreter").

## Configure `Launcher/config.json`
Edit `Launcher/config.json` to specify your algorithm details. Example:
```json
{
"algorithm-type-name": "BasicTemplateAlgorithm",
"algorithm-language": "Python",
"algorithm-location": "../../../Algorithm.Python/BasicTemplateAlgorithm.py",
"debugging": true,
"debugging-method": "Debugpy"
}
```
- `"algorithm-type-name"`: The class name containing the `Initialize` function.
- `"algorithm-language"`: Set to `"Python"`.
- `"algorithm-location"`: Path to your Python algorithm script.
- `"debugging"`: `true` to enable debugging.
- `"debugging-method"`: `"Debugpy"` for Python.

## Compile QuantConnect
Compile Lean using one of these commands:

**Debug Mode:**
```bash
dotnet build QuantConnect.Lean.sln
```

**Release Mode:**
```bash
dotnet build QuantConnect.Lean.sln -c Release
```
If using Release mode, update all instances of "Debug" to "Release" in related settings (e.g., `.vscode/launch.json`).

## Launching and Debugging
To run and debug a QuantConnect algorithm:

1. In VS Code, open "Run and Debug" (Ctrl+Shift+D).
2. Select the "launch" configuration (usually named "Launch Lean").
3. Click the "play" icon (Start Debugging).
4. Wait for the console to display "Debugger listening on port 5678" (or similar).
5. Set breakpoints in your Python algorithm code (optional).
6. Select the "Attach to Python" configuration (usually named "Python: Attach to Lean") and click play.

No graphical charts are displayed during local debugging; results like Sharpe ratio appear in the console after backtesting.

## Disabling Pre-Launch Build
To skip automatic rebuilding when no changes are made to C# files or `Launcher/config.json`, comment out the `preLaunchTask` in `.vscode/launch.json`:
```json
// "preLaunchTask": "build",
```
If changes are made, manually recompile as described in [Compile QuantConnect](#compile-quantconnect).

## QuantBook Research
To use QuantBook for research:

1. Ensure your Python virtual environment (`.venv`) is activated.
2. Open a Git Bash terminal (or any shell supporting `.sh` scripts if `launch_research.sh` is a shell script, otherwise a regular terminal).
3. Navigate to the Lean project root directory.
4. Launch Jupyter Lab or Notebook:
```bash
jupyter lab
# or
# jupyter notebook
```
5. In Jupyter, open `BasicQuantBookTemplate.ipynb` (usually found in `Algorithm.Python/`).
6. Ensure the kernel selected for the notebook is "Python (Lean Venv)" (or the display name you chose).
7. In the first cell, execute:
```python
%run start.py
```
*(Note: `start.py` might be in the same directory as the notebook or in the Lean root. Adjust path if necessary. The `launch_research.sh` script might set up necessary environment variables or paths; if not using it, ensure `QuantBook` can find the Lean engine components.)*

---

# Downloading IBKR Data & Converting to LEAN Format

This section details how to download 1-minute historical data from Interactive Brokers (IBKR) using the `download_ibkr_data.py` script and then convert it into the Lean-compatible format for local backtesting using `convert_ibkr_to_lean.py`.

## Prerequisites
- **Python 3.11 (or compatible)** installed and configured in a virtual environment.
- **Required Python Libraries:**
- `ibapi` (Interactive Brokers API client)
- `pandas`
- `pytz`
Install them into your activated virtual environment:
```bash
pip install ibapi pandas pytz
```
- **Interactive Brokers Account**: Requires market data subscriptions for the desired instruments and data types (e.g., Level 1 for trades, Level 1 for quotes).
- **IBKR Trader Workstation (TWS) or IB Gateway**:
- Must be running and logged in.
- API access enabled: In TWS/Gateway, go to **File > Global Configuration > API > Settings** (or **Edit > Global Configuration...** depending on version).
- Check "Enable ActiveX and Socket Clients".
- Note the "Socket port" (e.g., `7497` for paper TWS, `7496` for live TWS, `4002` for paper Gateway, `4001` for live Gateway).
- Optionally, add `127.0.0.1` to "Trusted IP Addresses" if running the script on the same machine.
- **QuantConnect LEAN Engine**: Assumes Lean is set up as per the "QuantConnect Local Lean Setup" section.

---

## Part 1: Downloading 1-Minute Data from IBKR
This part uses the Python script `download_ibkr_data.py`.

### Step 1.1: Prepare Configuration File (`datadownload_config.json`)
Create a JSON file named `datadownload_config.json` in the same directory as `download_ibkr_data.py`. This file specifies all parameters for the data download.

**Example `datadownload_config.json` (for Equity Trades - AAPL):**
```json
{
"contract_details": {
"symbol": "AAPL",
"secType": "STK",
"exchange": "SMART",
"currency": "USD",
"primaryExchange": "NASDAQ" // Optional, but good for US stocks
},
"instrument_timezone": "America/New_York", // Timezone of the exchange where the instrument primarily trades
"date_range": {
"start_year": 2023,
"start_month": 1,
"start_day": 1,
"end_year": 2023,
"end_month": 12,
"end_day": 31
},
"api_settings": {
"ibkr_port": 7497, // Match your TWS/Gateway port
"client_id": 10, // Any unique integer for this script instance
"max_active_requests": 5, // Max concurrent data requests to IBKR
"inter_request_delay_seconds": 11, // Delay between sending new requests (be mindful of IBKR pacing limits)
"max_connect_retries": 5,
"connect_retry_delay_seconds": 30,
"max_request_retries": 3,
"request_retry_delay_seconds": 20, // Renamed from request_retries_delay_seconds for consistency
"use_rth": 1, // 1 for Regular Trading Hours only (common for stocks), 0 for all hours (common for crypto/futures)
"what_to_show": "TRADES" // Data type: TRADES, AGGTRADES, MIDPOINT, BID, ASK, BID_ASK
},
"output": {
"filename_template": "{symbol}_{what_to_show_lowercase}_1min_{start_year}-{end_year}_utc.csv"
}
}
```

**Key Configuration Fields for `datadownload_config.json`:**

- **`contract_details`**: Defines the financial instrument.
- `symbol`: Ticker symbol (e.g., "AAPL", "ES", "EUR", "BTC").
- `secType`: Security Type (e.g., "STK", "FUT", "CASH", "CRYPTO", "IND").
- `exchange`: Destination exchange. Use "SMART" for stocks to let IBKR route. For specific exchanges like "CME" (futures), "IDEALPRO" (forex), "PAXOS" (crypto), specify directly.
- `currency`: Currency of the instrument (e.g., "USD", "EUR", "HKD").
- `primaryExchange`: (Optional, mainly for STK) Helps disambiguate stocks with same symbol on different exchanges. E.g., "NASDAQ", "NYSE", "ARCA".
- `lastTradeDateOrContractMonth`: (Required for FUT/OPT) Format "YYYYMM" or "YYYYMMDD".
- `multiplier`: (For FUT/OPT) Contract multiplier.
- `right`: (For OPT) "C" for Call, "P" for Put.
- `strike`: (For OPT) Option strike price.
- **`instrument_timezone`**: IANA timezone string for the instrument's primary trading exchange (e.g., "America/New_York", "Europe/London", "Asia/Hong_Kong", "UTC" for crypto). Used for interpreting ambiguous timestamps if IBKR doesn't provide a timezone.
- **`date_range`**: Specifies the period for data download.
- `start_year`, `start_month`, `start_day`: Beginning of the range.
- `end_year`, `end_month`, `end_day`: End of the range (inclusive).
- **`api_settings`**: Controls connection and request behavior.
- `ibkr_port`: Socket port of your running TWS or IB Gateway.
- `client_id`: A unique integer ID for this API client connection.
- `max_active_requests`: Maximum number of concurrent historical data requests. IBKR has limits.
- `inter_request_delay_seconds`: Pause between initiating new data requests to avoid pacing violations.
- `max_connect_retries`, `connect_retry_delay_seconds`: For initial connection attempts.
- `max_request_retries`, `request_retry_delay_seconds`: For retrying individual data requests that fail.
- `use_rth`:
- `1`: Request data only within Regular Trading Hours (RTH). Suitable for most stocks.
- `0`: Request all available data, including outside RTH. Essential for instruments trading 24/7 (e.g., crypto, some futures) or if you need pre/post-market data.
- `what_to_show`: Type of data to download. Common values:
- `"TRADES"`: Individual last trade data.
- `"AGGTRADES"`: Aggregated trades (often required for crypto like BTC on PAXOS).
- `"MIDPOINT"`: Midpoint of bid/ask.
- `"BID"`: Bid prices.
- `"ASK"`: Ask prices.
- `"BID_ASK"`: Both bid and ask data. The script `download_ibkr_data.py` needs to be adapted to handle the two-sided data if this option is chosen and save it appropriately (e.g., separate columns for bid and ask).
*(Self-correction: The original markdown mentioned `download_settings.download_type`. The script `download_ibkr_data.py` has been updated to use `api_settings.what_to_show` for this purpose. If `BID_ASK` is chosen, the script would need internal logic to handle and store bid/ask columns, which might not be in the current version of `download_ibkr_data.py` unless explicitly added.)*
- **`output`**:
- `filename_template`: A Python f-string like template for the output CSV filename. Available placeholders: `{symbol}`, `{what_to_show_lowercase}`, `{secType_lowercase}`, `{start_year}`, `{end_year}`. Example: `"{symbol}_{what_to_show_lowercase}_1min_{start_year}-{end_year}_utc.csv"` will produce filenames like `AAPL_trades_1min_2023-2023_utc.csv`.

**Example `datadownload_config.json` for BTC (Crypto Trades):**
```json
{
"contract_details": {
"symbol": "BTC",
"secType": "CRYPTO",
"exchange": "PAXOS", // Or "GEMINI" depending on IBKR routing
"currency": "USD"
},
"instrument_timezone": "UTC", // Crypto trades 24/7, UTC is a good default
"date_range": {
"start_year": 2022, // Adjust to a period where data is known to be available
"start_month": 1,
"start_day": 1,
"end_year": 2023,
"end_month": 12,
"end_day": 31
},
"api_settings": {
"ibkr_port": 4002, // Example: IB Gateway paper trading port
"client_id": 11,
"max_active_requests": 2, // Lower for crypto is often safer
"inter_request_delay_seconds": 16, // Higher delay for crypto
"use_rth": 0, // MUST be 0 for crypto
"what_to_show": "AGGTRADES" // Often required for BTC on PAXOS
},
"output": {
"filename_template": "{symbol}_{what_to_show_lowercase}_1min_{start_year}-{end_year}_utc.csv"
}
}
```

**Important Notes for `datadownload_config.json`:**
- **Data Availability:** IBKR's historical data depth varies by instrument, data type (`what_to_show`), and bar size. For 1-minute bars, data might only go back a few years or less for some instruments. Start with recent dates if you encounter "no data" errors.
- **`what_to_show` for `BID_ASK`:** If you intend to download separate bid and ask data (e.g., for creating quote bars), the `download_ibkr_data.py` script must be specifically designed to handle this. It would need to make two requests (one for `BID`, one for `ASK`) or process the `BID_ASK` stream appropriately. The current version might primarily focus on single-stream data like `TRADES` or `AGGTRADES`. If `what_to_show` is set to `"BID_ASK"`, the script would save columns like `bid_open`, `bid_high`, `ask_low`, `ask_close`. The converter script `convert_ibkr_to_lean.py` would then need to correctly identify these columns to generate Lean quote files.
- **Pacing Violations:** Requesting too much data too quickly can lead to temporary blocks from IBKR (Error 162 can sometimes indicate this, not just "no data"). Adjust `max_active_requests` and `inter_request_delay_seconds` if you encounter issues.

### Step 1.2: Run TWS or IB Gateway
Ensure your Trader Workstation (TWS) or IB Gateway application is:
- Running on your machine.
- Logged into your IBKR account (live or paper).
- API settings are correctly configured (as per Prerequisites).

> **Note**: IBKR typically allows only one active API connection per client ID and often one TWS/Gateway session per user at a time. Ensure no other scripts or platforms are using the same client ID or that your account login isn't active elsewhere if you face connection issues.

### Step 1.3: Execute the Download Script
1. Open a terminal or command prompt.
2. Activate your Python virtual environment (e.g., `.venv\Scripts\activate`).
3. Navigate to the directory containing `download_ibkr_data.py` and your `datadownload_config.json`.
4. Run the script:
```bash
python download_ibkr_data.py
```

The script will perform the following actions:
- Load settings from `datadownload_config.json`.
- Attempt to connect to TWS/Gateway using the specified port and client ID.
- Request historical 1-minute data in chunks (typically weekly, based on `durationStr="1 W"` inside the script).
- Convert timestamps of received bars to UTC.
- Append data to a CSV file named according to the `output.filename_template`.
- If the script is interrupted, it will attempt to resume downloading from the last recorded date in the CSV file upon restart.

Monitor the console output for progress, connection status, data request details, and any error messages.

---

## Part 2: Converting IBKR Data to LEAN Format
This part uses the Python script `convert_ibkr_to_lean.py` to transform the CSV data downloaded by `download_ibkr_data.py` into the format required by the QuantConnect LEAN engine.

### Step 2.1: Prepare Market Configuration File (`market_config.json`)
Create a JSON file named `market_config.json` in the same directory as `convert_ibkr_to_lean.py` (or specify its path to the script if it supports that). This file defines market hours, timezones, and weekend days for different exchanges/markets.

**Example `market_config.json`:**
```json
{
"usa_stk": { // Market code for US Stocks
"timezone": "America/New_York",
"marketStart": "09:30:00",
"marketEnd": "16:00:00",
// Optional pre/post market, not strictly needed for Lean minute data conversion if RTH is used for download
"preMarketStart": "04:00:00",
"preMarketEnd": "09:30:00",
"postMarketStart": "16:00:00",
"postMarketEnd": "20:00:00",
"weekendDays": [5, 6] // 0=Monday, ..., 5=Saturday, 6=Sunday
},
"paxos_crypto": { // Market code for Crypto on PAXOS
"timezone": "UTC", // Crypto trades 24/7
"marketStart": "00:00:00",
"marketEnd": "23:59:59", // Effectively all day
"weekendDays": [] // No weekend closure
},
"cme_fut": { // Market code for CME Futures (example for ES)
"timezone": "America/Chicago", // CME is based in Chicago
"marketStart": "17:00:00", // Sunday 5 PM CT start for the week
"marketEnd": "16:00:00", // Friday 4 PM CT end for the week (adjust for specific future)
"weekendDays": [5], // Saturday is off; Sunday trading starts in evening
"dailyClose": "16:00:00" // Example daily settlement/close time
}
// Add other markets as needed
}
```

**Key Fields for each market entry:**
- `"market_code_key"` (e.g., `"usa_stk"`, `"paxos_crypto"`): A unique string you'll use to refer to this market configuration (matches the `-m` argument for the conversion script).
- `timezone`: IANA timezone string for the market (e.g., "America/New_York", "UTC").
- `marketStart`, `marketEnd`: Regular trading session start and end times in `HH:MM:SS` format, local to the `timezone`.
- `preMarketStart`, `preMarketEnd`, `postMarketStart`, `postMarketEnd`: (Optional) Define pre/post-market sessions if relevant.
- `weekendDays`: A list of integers representing weekend days (0 for Monday, 6 for Sunday) where the market is typically closed. For 24/7 markets, this can be an empty list `[]`.

### Step 2.2: Execute the Conversion Script
Run the `convert_ibkr_to_lean.py` script from your terminal, providing the necessary arguments.

```bash
python convert_ibkr_to_lean.py [INPUT_CSV_PATH] [OUTPUT_LEAN_DATA_DIR] [TICKER] -m [MARKET_CODE] -s [SEC_TYPE]
```

**Command Line Arguments:**
- `[INPUT_CSV_PATH]`: Full path to the CSV file downloaded by `download_ibkr_data.py` (e.g., `AAPL_trades_1min_2023-2023_utc.csv`).
- `[OUTPUT_LEAN_DATA_DIR]`: The root directory where Lean-formatted data will be saved (e.g., `./data` or your Lean project's `Data` folder).
- `[TICKER]`: The symbol of the instrument (e.g., `AAPL`, `BTCUSD`, `ES`). This will be converted to lowercase for directory names.
- `-m [MARKET_CODE]`: The market code (key) from your `market_config.json` that corresponds to this instrument (e.g., `usa_stk`, `paxos_crypto`). Defaults to a pre-defined value like `usa` if omitted and supported by the script.
- `-s [SEC_TYPE]`: The security type (e.g., `equity`, `crypto`, `future`). This determines the subdirectory structure within `[OUTPUT_LEAN_DATA_DIR]` (e.g., `equity`, `crypto`, `future`).

**Example Usage:**

* **For AAPL (US Stock, Trade Data):**
```bash
python convert_ibkr_to_lean.py AAPL_trades_1min_2023-2023_utc.csv ./lean_data equity aapl -m usa_stk
```
* **For BTCUSD (Crypto, Aggregated Trade Data):**
Assume downloaded CSV is `BTC_aggtrades_1min_2022-2023_utc.csv`.
```bash
python convert_ibkr_to_lean.py BTC_aggtrades_1min_2022-2023_utc.csv ./lean_data crypto btcusd -m paxos_crypto
```
*(Note: The `TICKER` argument for the converter might be `btc` or `btcusd` depending on how you want Lean to recognize it. For crypto, Lean often uses the pair like `btcusd`.)*

**The conversion script will typically:**
1. Read the input CSV data and the specified market configuration from `market_config.json`.
2. Process the data row by row:
- Convert UTC timestamps from the CSV to the local market time based on the market's `timezone`.
- **Timestamp Convention:** IBKR data is often timestamped at the *beginning* of the bar interval. LEAN expects timestamps at the *end* of the bar interval. The script should adjust for this (e.g., by adding 1 minute to 1-minute bar timestamps).
- **Price Scaling:** LEAN stores equity prices as integers, scaled by 10,000 (e.g., a price of $150.25 becomes 1502500). The script must apply this scaling. Crypto and Forex might not require this scaling or use a different convention.
3. Generate daily data files, zipped, in the LEAN directory structure:
```
[OUTPUT_LEAN_DATA_DIR]/[security_type]/[market_code_from_arg_or_default]/minute/[ticker_lowercase]/[YYYYMMDD]_{datatype}.zip
```
- `{security_type}`: e.g., `equity`, `crypto`, `future`.
- `{market_code_from_arg_or_default}`: e.g., `usa_stk` (or just `usa` if the script defaults).
- `{ticker_lowercase}`: e.g., `aapl`, `btcusd`.
- `{datatype}`: `trade` if the input CSV contained trade data (OHLCV), or `quote` if it contained bid/ask data (BidOHLC, AskOHLC).
**Example:** `./lean_data/equity/usa_stk/minute/aapl/20230103_trade.zip` or `./lean_data/crypto/paxos_crypto/minute/btcusd/20220101_trade.zip` (if `AGGTRADES` is treated as trade type). If input was bid/ask, it would be `..._quote.zip`.
4. Create a `map_files` entry if applicable (mainly for equities, to handle symbol changes, splits, dividends). For simple downloads, it might create a basic map file.
```
[OUTPUT_LEAN_DATA_DIR]/[security_type]/[market_code_from_arg_or_default]/map_files/[ticker_lowercase].csv
```
5. Optionally, output a market hours database entry snippet (e.g., `usa_stk_market_hours_entry.json`) that can be used to update LEAN's internal database.

**Note on Data Types (Trade vs. Quote) for Conversion:**
The `convert_ibkr_to_lean.py` script needs to intelligently detect the type of data in the input CSV.
- If columns like `open`, `high`, `low`, `close`, `volume` are present, it's **trade data**.
- If columns like `bid_open`, `bid_high`, `bid_low`, `bid_close`, `last_bid_size`, `ask_open`, `ask_high`, `ask_low`, `ask_close`, `last_ask_size` are present (or similar naming from `download_ibkr_data.py` if it handles `BID_ASK`), it's **quote data**.
The converter will then generate `_trade.zip` or `_quote.zip` files accordingly.

### Step 2.3: Update LEAN's Market Hours Database (Manual Step)
This step is **critical** for ensuring LEAN uses the correct trading hours for your custom data, especially for markets not natively configured in detail within LEAN.

1. If the `convert_ibkr_to_lean.py` script generates a market hours entry JSON snippet (e.g., `usa_stk_market_hours_entry.json`):
- Open this generated file and copy its entire JSON content.
2. Locate LEAN's main market hours database file. This is typically found at:
`[YourLeanProjectRoot]/Data/market-hours/market-hours-database.json`
3. Open `market-hours-database.json` in a text editor.
4. Find the main `"entries"` JSON object within this file.
5. Carefully paste the copied content as a new key-value pair within the `"entries"` object.
- The key should be the market identifier LEAN uses (e.g., `"usa"`, `"hkfe"`).
- The value will be the JSON structure you copied.
- If an entry for that market already exists, you may need to merge your definitions or replace the existing one if yours is more accurate or specific for your data.
**Example structure within `market-hours-database.json`:**
```json
{
// ... other database content ...
"entries": {
"usa": { /* ... existing USA market hours ... */ },
"india": { /* ... existing India market hours ... */ },
// Your pasted entry might look like this:
"hkfe": { // This key "hkfe" must match how Lean refers to this market
"exchangeTimeZone": "Asia/Hong_Kong",
"hours": [
{ "dayOfWeek": "monday", "marketOpen": "09:30:00", "marketClose": "16:00:00", "preMarketOpen": "09:00:00", "postMarketClose": "16:10:00" },
// ... entries for Tuesday to Friday ...
]
}
// ... other entries ...
}
}
```
Ensure the JSON remains valid after pasting.

### Step 2.4: Handling Markets Not Natively Supported by LEAN (e.g., HKFE)
If you are working with a market that LEAN doesn't have built-in detailed support for (beyond basic entries in `market-hours-database.json`), you might need to modify LEAN's C# source code. This is an advanced step.
The general approach is to find how an existing, well-supported market (like `Market.India` or `Market.USA`) is defined and referenced, and then replicate that structure for your new market (e.g., `Market.HKFE`).

**Example C# Modifications (Illustrative for a hypothetical HKFE market):**

1. **In `Common/Market.cs` (or a similar file defining market enumerations/identifiers):**
Ensure your market has a string identifier.
```csharp
public static class Market
{
// ... existing markets ...
public const string HKFE = "hkfe"; // Add your market identifier
}
```

2. **In `Common/Exchange.cs` (or where `Exchange` objects are defined):**
Define an `Exchange` instance for your market.
```csharp
public static partial class Exchange
{
// ... existing exchanges ...
public static readonly Exchange HKFE = new Exchange("HKFE", Market.HKFE, SecurityType.Equity, "Hong Kong Futures Exchange");
// Adjust SecurityType if it's not Equity, and the description.
}
```
*(Self-correction: The original example had `QuantConnect.Market.HKFE` which implies a different structure. The key is to ensure `Market.HKFE` resolves to the correct string identifier and `Exchange.HKFE` is properly instantiated.)*

3. **In `Common/Global.cs` (or `SymbolCache.cs` or similar, where market strings are mapped to `Exchange` objects):**
Add a case to map the string identifier to your `Exchange` object.
```csharp
// In a method like GetExchange(string market)
switch (market.ToLowerInvariant())
{
// ... existing cases ...
case Market.HKFE: return Exchange.HKFE;
}
```

4. **In `Engine/DataFeeds/ApiDataProviders.cs` (or similar, if LEAN tries to fetch map/factor files from its API for this market):**
If LEAN's data provider attempts to download auxiliary files (like map files or factor files) from QuantConnect's API for your custom market, and you are providing these locally, you might need to add a condition to bypass this check or handle it.
The original example showed adding a check to throw an exception if not subscribed. If you are providing all data locally, you might want to ensure these checks are skipped for your custom market if it's not a QC-supported dataset.

```csharp
// Example logic:
if (market.Equals(Market.HKFE, StringComparison.InvariantCultureIgnoreCase) &&
(securityType == SecurityType.Equity || IsAuxiliaryData(filePath)))
{
// If providing local map/factor files, this check might need to be bypassed
// or handled differently than for QC datasets.
// For purely local data, you might not need this specific block.
// The original example was for *preventing* download if not subscribed.
}
```

**General Advice for Custom Markets:**
- **Search the Codebase:** Use "Find in Files" in your IDE to search for how `Market.USA` or `Market.India` (or another market of the same `SecurityType` as yours) is implemented. This will reveal all the places you might need to add definitions for your custom market.
- **Start Simple:** First, ensure your data is correctly formatted and that `market-hours-database.json` is updated. Test if LEAN can read the data. C# modifications are often only needed for full integration or if LEAN's default behaviors for unknown markets are problematic.
- **Debugging:** If LEAN fails to load your data, check the console logs carefully. You can add print statements (`Log.Trace(...)` in C# or `self.Log(...)` in Python algorithms) to see what data LEAN is attempting to load and from where. In your Python algorithm, you can try `self.History()` requests to see if the data loads.

By following these updated instructions, you should have a clearer path to downloading data with `download_ibkr_data.py` and then converting and integrating it into your local LEAN environment. Remember that data availability and correct configuration are key.
