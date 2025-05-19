import pandas as pd
import pytz
import os
import zipfile
from pathlib import Path
import argparse
from collections import Counter
import datetime
import json

def convert_ibkr_to_lean(input_csv_path, output_data_folder, ticker, market="hkfe"):
    """
    Converts an IBKR 1-minute data CSV (UTC timestamps) to LEAN format
    (daily zipped CSVs with local market time millisecond timestamps)
    and generates a market hours JSON entry.
    Handles both TRADES and BID_ASK (quote) data.

    Args:
        input_csv_path (str): Path to the input CSV file.
        output_data_folder (str): The root LEAN data folder.
        ticker (str): The stock ticker symbol.
        market (str): The market code used by LEAN.
    """
    print(f"Starting conversion for {ticker} from {input_csv_path}...")

    # --- Load Market Configuration ---
    config_path = Path(__file__).parent / "market_config.json"
    market_config = None
    target_tz_str = None
    target_tz = None

    try:
        with open(config_path, 'r') as f:
            market_configs = json.load(f)
        if market not in market_configs:
            print(f"Error: Market '{market}' not found in {config_path}. Please add its configuration.")
            return
        market_config = market_configs[market]
        target_tz_str = market_config.get("timezone")
        if not target_tz_str:
             print(f"Error: 'timezone' not defined for market '{market}' in {config_path}")
             return
        try:
            target_tz = pytz.timezone(target_tz_str)
        except pytz.exceptions.UnknownTimeZoneError:
            print(f"Error: Unknown timezone '{target_tz_str}' specified for market '{market}' in {config_path}.")
            return
        required_keys = ["marketEnd", "preMarketStart", "preMarketEnd", "marketStart", "postMarketStart", "postMarketEnd", "weekendDays"]
        for key in required_keys:
            if key not in market_config:
                print(f"Error: Required key '{key}' not found for market '{market}' in {config_path}.")
                return
        print(f"Loaded configuration for market '{market}', timezone: {target_tz_str}")
    except Exception as e:
        print(f"Error loading market configuration: {e}")
        return

    # --- Read Input CSV ---
    try:
        df = pd.read_csv(input_csv_path, parse_dates=['date'], date_parser=lambda x: pd.to_datetime(x, utc=True))
        if df.empty:
            print(f"Warning: Input file {input_csv_path} is empty.")
        print(f"Read {len(df)} rows from {input_csv_path}")
    except FileNotFoundError:
        print(f"Error: Input file not found at {input_csv_path}")
        return
    except Exception as e:
        print(f"Error reading CSV: {e}")
        return

    # --- Determine Data Type (Trade or Quote) ---
    is_quote_data = False
    if 'bid_open' in df.columns and 'ask_close' in df.columns:
        is_quote_data = True
        print("Detected QUOTE data (bid_open, ask_close columns found).")
    elif 'open' in df.columns and 'close' in df.columns and 'volume' in df.columns:
        is_quote_data = False
        print("Detected TRADE data (open, close, volume columns found).")
    else:
        if not df.empty:
            print(f"Error: Could not determine data type from columns: {df.columns.tolist()}")
            print("Expected trade columns (e.g., open, high, low, close, volume) or quote columns (e.g., bid_open, bid_high, ask_low, ask_close).")
            return
        else: # If df is empty, we can't determine type, but might still proceed for market hours
            print("Warning: DataFrame is empty, cannot determine data type from columns. Market hours generation will proceed if possible.")


    # Convert UTC datetime to target market timezone (This is the START of the bar from IBKR)
    if not df.empty:
        df['local_datetime_start'] = df['date'].dt.tz_convert(target_tz)
        # LEAN timestamp represents the END of the bar
        df['local_datetime_bar_end'] = df['local_datetime_start'] + pd.Timedelta(minutes=1)
    else:
        df['local_datetime_start'] = pd.Series(dtype='datetime64[ns, UTC]').dt.tz_localize(None).dt.tz_localize(target_tz)
        df['local_datetime_bar_end'] = pd.Series(dtype='datetime64[ns, UTC]').dt.tz_localize(None).dt.tz_localize(target_tz)


    # --- Analyze Holidays and Early Closes (based on bar start times) ---
    trading_dates = set()
    holidays_derived = []
    early_closes_derived = {}

    standard_market_end_str = market_config["marketEnd"]
    try:
        standard_market_end_time = datetime.datetime.strptime(standard_market_end_str, "%H:%M:%S").time()
    except ValueError:
        print(f"Error: Invalid 'marketEnd' time format '{standard_market_end_str}'. Expected HH:MM:SS.")
        return

    if not df.empty:
        # Group by the date of the bar's START time for holiday/early close analysis
        daily_groups = df.groupby(df['local_datetime_start'].dt.date)
        for date_obj, group in daily_groups:
            trading_dates.add(date_obj)
            # The end of trading for this day is the end of the last bar that started on this day
            actual_day_end_dt_for_analysis = group['local_datetime_bar_end'].max()
            actual_day_end_time_for_analysis = actual_day_end_dt_for_analysis.time()

            # If the last bar ends exactly at midnight, it might be part of the next day's session start
            # We are checking if the trading *session* ended early.
            # The marketEnd time refers to the time the regular session closes.
            if actual_day_end_time_for_analysis < standard_market_end_time:
                # Check if it's not just a bar ending before midnight but still within market hours
                # e.g. market ends 16:00, last bar ends 15:59 - this is not an early close.
                # last bar ends 12:00 - this is an early close.
                # The comparison should be with the *expected* end time of the last bar.
                # For simplicity, we assume if the max bar end time is before standard market end, it's an early close.
                # This might need refinement if market has fixed closing auction times etc.
                date_key = f"{date_obj.month}/{date_obj.day}/{date_obj.year}"
                early_closes_derived[date_key] = actual_day_end_time_for_analysis.strftime("%H:%M:%S")


        all_dates_in_range = set()
        if not df.empty:
            start_date_for_range = df['local_datetime_start'].min().date()
            end_date_for_range = df['local_datetime_start'].max().date()
            current_date_iter = start_date_for_range
            while current_date_iter <= end_date_for_range:
                all_dates_in_range.add(current_date_iter)
                current_date_iter += datetime.timedelta(days=1)

            missing_dates = sorted(list(all_dates_in_range - trading_dates))
            configured_weekend_days_py = set(market_config["weekendDays"])

            for missing_date in missing_dates:
                if missing_date.weekday() not in configured_weekend_days_py:
                    holidays_derived.append(missing_date)
    else:
        print("Warning: Input DataFrame is empty. Cannot analyze holidays or early closes from data.")

    holidays_json_format = [f"{d.month}/{d.day}/{d.year}" for d in holidays_derived]

    # --- Generate JSON structure for market-hours-database.json ---
    sessions_json = {}
    py_to_json_dow_map = {0: "monday", 1: "tuesday", 2: "wednesday", 3: "thursday", 4: "friday", 5: "saturday", 6: "sunday"}
    configured_weekend_days_py = set(market_config["weekendDays"]) # Already loaded

    for py_dow in range(7):
        json_dow_name = py_to_json_dow_map[py_dow]
        if py_dow in configured_weekend_days_py:
            sessions_json[json_dow_name] = []
        else:
            sessions_json[json_dow_name] = [
                {"start": market_config["preMarketStart"], "end": market_config["preMarketEnd"], "state": "premarket"},
                {"start": market_config["marketStart"], "end": market_config["marketEnd"], "state": "market"},
                {"start": market_config["postMarketStart"], "end": market_config["postMarketEnd"], "state": "postmarket"}
            ]

    market_hours_entry_key = f"Equity-{market}-[*]" # Generic entry for all equities in the market
    if ticker: # If a specific ticker is provided, we can make the key more specific, though LEAN often uses [*]
        market_hours_entry_key = f"Equity-{market}-{ticker.upper()}"


    market_hours_entry = {
        market_hours_entry_key: { # Use the determined key
            "dataTimeZone": target_tz_str,
            "exchangeTimeZone": target_tz_str,
            "sessions": sessions_json,
            "holidays": holidays_json_format,
            "earlyCloses": early_closes_derived
        }
    }

    json_entry_filename = f"{market}_{ticker.lower()}_market_hours_entry.json"
    script_dir = Path(__file__).parent
    json_output_path = script_dir / json_entry_filename
    try:
        with open(json_output_path, 'w') as f:
            json.dump(market_hours_entry, f, indent=2)
        print(f"\nSaved market hours JSON entry to: {json_output_path}")
        print(f"\nIMPORTANT: Manually copy the content of this file and merge/replace")
        print(f"the entry for '{market_hours_entry_key}' in 'data/market-hours/market-hours-database.json'.")
        print(f"If using a generic key like 'Equity-{market}-[*]', ensure it's appropriate for your LEAN setup.")

    except Exception as e:
        print(f"\nError writing market hours JSON entry file: {e}")


    # --- Data Conversion for LEAN ---
    if df.empty:
        print("Skipping LEAN data file generation as input DataFrame is empty.")
        return

    # Calculate timestamp in milliseconds since local midnight of the bar's ENDING day.
    midnight_local_bar_end_day = df['local_datetime_bar_end'].dt.normalize()
    time_since_midnight_local = df['local_datetime_bar_end'] - midnight_local_bar_end_day
    df['time_ms'] = (time_since_midnight_local.dt.total_seconds() * 1000).astype(int)
    print("Calculated milliseconds since local midnight of bar's ending day.")

    lean_output_dir = Path(output_data_folder) / 'equity' / market / 'minute' / ticker.lower()
    lean_output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory for LEAN files: {lean_output_dir}")

    if is_quote_data:
        print("Processing for LEAN QUOTE format...")
        # LEAN Quote columns: Time,BidOpen,BidHigh,BidLow,BidClose,LastPrice,Volume,BidSize,AskSize,AskOpen,AskHigh,AskLow,AskClose
        # IBKR CSV columns: date,bid_open,bid_high,ask_low,ask_close

        df_lean = pd.DataFrame()
        df_lean['time_ms'] = df['time_ms']
        df_lean['bid_open'] = (df['bid_open'] * 10000).round().astype(int)
        df_lean['bid_high'] = (df['bid_high'] * 10000).round().astype(int)
        df_lean['bid_low'] = df_lean['bid_open'] # Approximation
        df_lean['bid_close'] = df_lean['bid_open'] # Approximation

        df_lean['last_price'] = 0 # No trade info in IBKR quote bars
        df_lean['last_trade_volume'] = 0 # No trade info

        df_lean['bid_size'] = 0 # Not available in IBKR aggregated bars
        df_lean['ask_size'] = 0 # Not available

        df_lean['ask_open'] = (df['ask_low'] * 10000).round().astype(int) # Approximation
        df_lean['ask_high'] = (df['ask_close'] * 10000).round().astype(int) # Approximation
        df_lean['ask_low'] = (df['ask_low'] * 10000).round().astype(int)
        df_lean['ask_close'] = (df['ask_close'] * 10000).round().astype(int)

        # Ensure correct column order for LEAN quote format
        lean_quote_columns = [
            'time_ms', 'bid_open', 'bid_high', 'bid_low', 'bid_close',
            'last_price', 'last_trade_volume',
            'bid_size', 'ask_size',
            'ask_open', 'ask_high', 'ask_low', 'ask_close'
        ]
        df_lean = df_lean[lean_quote_columns]
        file_suffix = "quote"
        print("Prepared QUOTE DataFrame for LEAN.")

    else: # Trade Data
        print("Processing for LEAN TRADE format...")
        price_cols = ['open', 'high', 'low', 'close']
        for col in price_cols:
            df[col] = (df[col] * 10000).round().astype(int)
        print("Scaled prices by 10000 for trade data.")

        df_lean = df[['time_ms', 'open', 'high', 'low', 'close', 'volume']].copy()
        df_lean['volume'] = df_lean['volume'].astype(int)
        file_suffix = "trade"
        print("Prepared TRADE DataFrame for LEAN.")

    # Group by the date of the BAR'S END time for daily file creation
    grouped = df_lean.groupby(df['local_datetime_bar_end'].dt.date)

    for date_obj, group_df in grouped:
        date_str = date_obj.strftime('%Y%m%d')
        # LEAN Filename convention inside zip: {YYYYMMDD}_{ticker}_minute_{type}.csv
        csv_filename_in_zip = f"{date_str}_{ticker.lower()}_minute_{file_suffix}.csv"
        # LEAN Zip filename convention: {YYYYMMDD}_{type}.zip
        zip_filename = f"{date_str}_{file_suffix}.zip"
        zip_filepath = lean_output_dir / zip_filename

        csv_content = group_df.to_csv(index=False, header=False)
        try:
            with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(csv_filename_in_zip, csv_content)
            # print(f"  Created {zip_filepath} with {csv_filename_in_zip}")
        except Exception as e:
            print(f"  Error creating zip file {zip_filepath}: {e}")

    # --- Generate Map File (only if data was processed) ---
    if not df.empty:
        # Map file date should be the first date a bar STARTED on
        first_bar_start_date_obj = df['local_datetime_start'].min().date()
        first_date_str_for_map = first_bar_start_date_obj.strftime('%Y%m%d')

        map_file_dir = Path(output_data_folder) / 'equity' / market / 'map_files'
        map_file_path = map_file_dir / f"{ticker.lower()}.csv"
        # LEAN map file format: YYYYMMDD,mapped_symbol_for_that_date
        # For simplicity, assuming ticker doesn't change.
        map_content = f"{first_date_str_for_map},{ticker.lower()}\n20501231,{ticker.lower()}\n"

        try:
            map_file_dir.mkdir(parents=True, exist_ok=True)
            with open(map_file_path, 'w') as f:
                f.write(map_content)
            print(f"Generated map file: {map_file_path}")
        except Exception as e:
            print(f"Error generating map file {map_file_path}: {e}")

    print(f"\nConversion complete for {input_csv_path}.")
    print(f"LEAN formatted data saved in: {lean_output_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert IBKR 1-min data CSV to LEAN format and generate a market hours JSON entry.")
    parser.add_argument("input_csv", help="Path to the input CSV file (e.g., AAPL_trades_1min_2023-2023_utc.csv or EUR_bid_ask_1min_2023-2023_utc.csv)")
    parser.add_argument("output_dir", help="Path to the root LEAN data folder (e.g., ./data)")
    parser.add_argument("ticker", help="Stock ticker symbol for LEAN (e.g., aapl, eurusd). This will be lowercased.")
    parser.add_argument("-m", "--market", default="usa", help="LEAN market code (e.g., usa, hkfe, fxcm). Default: usa")

    args = parser.parse_args()
    Path(args.output_dir).mkdir(parents=True, exist_ok=True) # Ensure base output dir exists

    # Lowercase ticker for LEAN consistency
    lean_ticker = args.ticker.lower()

    convert_ibkr_to_lean(args.input_csv, args.output_dir, lean_ticker, args.market)
