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

    Args:
        input_csv_path (str): Path to the input CSV file.
        output_data_folder (str): The root LEAN data folder.
        ticker (str): The stock ticker symbol.
        market (str): The market code used by LEAN.
    """
    print(f"Starting conversion for {ticker} from {input_csv_path}...")

    # --- Load Market Configuration FIRST ---
    # This is now done first to get the timezone for data processing.
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
        print(f"Loaded configuration for market '{market}' from {config_path}")

        # Get and validate timezone from config
        target_tz_str = market_config.get("timezone")
        if not target_tz_str:
             print(f"Error: 'timezone' not defined for market '{market}' in {config_path}")
             return
        try:
            target_tz = pytz.timezone(target_tz_str)
            print(f"Using target timezone from config: {target_tz_str}")
        except pytz.exceptions.UnknownTimeZoneError:
            print(f"Error: Unknown timezone '{target_tz_str}' specified for market '{market}' in {config_path}.")
            return

        # Validate other critical market_config keys
        required_keys = ["marketEnd", "preMarketStart", "preMarketEnd", "marketStart", "postMarketStart", "postMarketEnd", "weekendDays"]
        for key in required_keys:
            if key not in market_config:
                print(f"Error: Required key '{key}' not found in market configuration for '{market}' in {config_path}.")
                return

    except FileNotFoundError:
        print(f"Error: Market configuration file not found at {config_path}")
        return
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from {config_path}")
        return
    except Exception as e:
        print(f"Error loading market configuration: {e}")
        return

    # --- Read Input CSV ---
    try:
        df = pd.read_csv(input_csv_path, parse_dates=['date'], date_parser=lambda x: pd.to_datetime(x, utc=True))
        if df.empty:
            print(f"Warning: Input file {input_csv_path} is empty. No data to process.")
            # Still generate an empty market hours entry if needed, or just return.
            # For now, let's allow it to proceed to generate a potentially empty market hours entry.
        print(f"Read {len(df)} rows from {input_csv_path}")
    except FileNotFoundError:
        print(f"Error: Input file not found at {input_csv_path}")
        return
    except Exception as e:
        print(f"Error reading CSV: {e}")
        return

    # Convert UTC datetime to target market timezone (This is the START of the bar from IBKR)
    if not df.empty:
        df['local_datetime_start'] = df['date'].dt.tz_convert(target_tz)
    else: # Handle empty df for subsequent operations
        df['local_datetime_start'] = pd.Series(dtype='datetime64[ns, UTC]').dt.tz_localize(None).dt.tz_localize(target_tz)


    # --- Analyze Holidays and Early Closes ---
    trading_dates = set()
    holidays_derived = [] 
    early_closes_derived = {} 

    standard_market_end_str = market_config["marketEnd"] # Already checked for existence
    try:
        standard_market_end_time = datetime.datetime.strptime(standard_market_end_str, "%H:%M:%S").time()
    except ValueError:
        print(f"Error: Invalid 'marketEnd' time format '{standard_market_end_str}' for market '{market}'. Expected HH:MM:SS.")
        return

    if not df.empty:
        daily_groups = df.groupby(df['local_datetime_start'].dt.date)
        for date_obj, group in daily_groups:
            trading_dates.add(date_obj)
            actual_day_end_dt = group['local_datetime_start'].max() + pd.Timedelta(minutes=1)
            actual_day_end_time = actual_day_end_dt.time()

            if actual_day_end_time < standard_market_end_time:
                date_key = f"{date_obj.month}/{date_obj.day}/{date_obj.year}"
                early_closes_derived[date_key] = actual_day_end_time.strftime("%H:%M:%S")

        all_dates_in_range = set()
        start_date = df['local_datetime_start'].min().date()
        end_date = df['local_datetime_start'].max().date()
        current_date = start_date
        while current_date <= end_date:
            all_dates_in_range.add(current_date)
            current_date += datetime.timedelta(days=1)

        missing_dates = sorted(list(all_dates_in_range - trading_dates))
        configured_weekend_days_py = set(market_config["weekendDays"]) # Already checked

        for missing_date in missing_dates:
            if missing_date.weekday() not in configured_weekend_days_py:
                holidays_derived.append(missing_date)
    else:
        print("Warning: Input DataFrame is empty. Cannot analyze holidays or early closes from data.")

    holidays_json_format = [f"{d.month}/{d.day}/{d.year}" for d in holidays_derived]

    # --- Generate JSON structure for market-hours-database.json ---
    sessions_json = {}
    py_to_json_dow_map = {
        0: "monday", 1: "tuesday", 2: "wednesday", 3: "thursday",
        4: "friday", 5: "saturday", 6: "sunday"
    }
    
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

    market_hours_entry = {
        f"Equity-{market}-[*]": {
            "dataTimeZone": target_tz_str, 
            "exchangeTimeZone": target_tz_str, 
            "sessions": sessions_json,
            "holidays": holidays_json_format,
            "earlyCloses": early_closes_derived
        }
    }

    json_entry_filename = f"{market}_market_hours_entry.json"
    script_dir = Path(__file__).parent
    json_output_path = script_dir / json_entry_filename
    try:
        with open(json_output_path, 'w') as f:
            json.dump(market_hours_entry, f, indent=2)
        print(f"\nSaved market hours JSON entry to: {json_output_path}")
        print("\nIMPORTANT: Manually copy the content of this file and merge/replace")
        print(f"the entry for 'Equity-{market}-[*]' in 'data/market-hours/market-hours-database.json'.")
    except Exception as e:
        print(f"\nError writing market hours JSON entry file: {e}")

    # --- Data Conversion for LEAN ---
    if df.empty:
        print("Skipping LEAN data file generation as input DataFrame is empty.")
        return # Exit if no data to process for LEAN files

    df['local_datetime_end'] = df['local_datetime_start'] + pd.Timedelta(minutes=1)
    print("Adjusted timestamps to represent END of bar for LEAN.")

    # Calculate timestamp in milliseconds since local midnight of the bar's ENDING day.
    midnight_local = df['local_datetime_end'].dt.normalize()
    time_since_midnight_local = df['local_datetime_end'] - midnight_local
    df['time_ms'] = (time_since_midnight_local.dt.total_seconds() * 1000).astype(int)
    print("Calculated milliseconds since local midnight of bar's ending day.")

    price_cols = ['open', 'high', 'low', 'close']
    for col in price_cols:
        df[col] = (df[col] * 10000).round().astype(int)
    print("Scaled prices by 10000 and converted to integer.")

    df_lean = df[['time_ms', 'open', 'high', 'low', 'close', 'volume']].copy()
    df_lean['volume'] = df_lean['volume'].astype(int)
    print("Prepared final DataFrame columns in order: time_ms, open, high, low, close, volume")

    lean_output_dir = Path(output_data_folder) / 'equity' / market / 'minute' / ticker.lower()
    lean_output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Ensured output directory exists: {lean_output_dir}")

    grouped = df_lean.groupby(df['local_datetime_end'].dt.date)

    for date_obj, group_df in grouped:
        date_str = date_obj.strftime('%Y%m%d')
        csv_filename = f"{date_str}_{ticker.lower()}_minute_trade.csv"
        zip_filename = f"{date_str}_trade.zip"
        csv_filepath_in_zip = csv_filename
        zip_filepath = lean_output_dir / zip_filename

        csv_content = group_df.to_csv(index=False, header=False)
        try:
            with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(csv_filepath_in_zip, csv_content)
        except Exception as e:
            print(f"  Error creating zip file {zip_filepath}: {e}")

    # --- Generate Map File ---
    # This check for df.empty is already implicitly handled by the return above if df is empty.
    # But explicit check here is also fine.
    if not df.empty: 
        first_date_obj = df['local_datetime_start'].min().date() # Use start time for map file date
        first_date_str = first_date_obj.strftime('%Y%m%d')
        map_file_dir = Path(output_data_folder) / 'equity' / market / 'map_files'
        map_file_path = map_file_dir / f"{ticker.lower()}.csv"
        map_content = f"{first_date_str},{ticker.lower()}\n20501231,{ticker.lower()}\n"

        try:
            map_file_dir.mkdir(parents=True, exist_ok=True)
            with open(map_file_path, 'w') as f:
                f.write(map_content)
            print(f"Generated map file: {map_file_path}")
        except Exception as e:
            print(f"Error generating map file {map_file_path}: {e}")
    # No explicit 'else' needed here as the case of empty df for map file is covered by the earlier return.

    print(f"\nConversion complete. LEAN formatted data saved in: {lean_output_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert IBKR 1-min data CSV to LEAN format and generate a market hours JSON entry.")
    parser.add_argument("input_csv", help="Path to the input CSV file (e.g., 1211_1min_2018_2025.csv)")
    parser.add_argument("output_dir", help="Path to the root LEAN data folder (e.g., ./data)")
    parser.add_argument("ticker", help="Stock ticker symbol (e.g., 1211)")
    parser.add_argument("-m", "--market", default="hkfe", help="LEAN market code (e.g., hkfe, usa). Default: hkfe")

    args = parser.parse_args()
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    convert_ibkr_to_lean(args.input_csv, args.output_dir, args.ticker, args.market)
