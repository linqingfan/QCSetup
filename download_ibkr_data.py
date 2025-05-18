from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.common import * # BarData is in here
import datetime
import time
import pandas as pd
import threading
import pytz
import os
import json

# --- Global Variables ---
bar_collection = {}
config = {}

# --- Constants ---
RETRYABLE_ERROR_CODES = {1100, 1101, 1102, 1300, 2103, 2105}
REQUEST_SPECIFIC_RETRYABLE_ERROR_CODES = {162, 321, 322}
INFORMATIONAL_API_CODES = {
    2100, 2104, 2106, 2107, 2108, 2150, 2158,
    2174, # Warning: You submitted request with date-time attributes without explicit time zone... (Handled by UTC format)
    10314 # End Date/Time: The date, time, or time-zone entered is invalid. (Hopefully handled by UTC format)
}
CSV_WRITE_ERROR_CODE = 999


class IBapi(EWrapper, EClient):
    def __init__(self, filename: str, expected_timezone_str: str = "America/New_York"):
        EClient.__init__(self, self)
        self.req_id_counter = 0
        self.filename = filename
        try:
            self.expected_timezone = pytz.timezone(expected_timezone_str)
        except pytz.exceptions.UnknownTimeZoneError:
            print(f"Warning: Unknown timezone '{expected_timezone_str}'. Defaulting to UTC.")
            self.expected_timezone = pytz.utc
        self.connection_lost = False
        self.is_connected_event = threading.Event()
        self.next_valid_req_id_ready = threading.Event()

    def nextValidId(self, orderId: int):
        super().nextValidId(orderId)
        self.req_id_counter = orderId
        print(f"NextValidId: {orderId}. Connection confirmed. Ready for requests.")
        self.is_connected_event.set()
        self.next_valid_req_id_ready.set()
        self.connection_lost = False

    def get_next_req_id(self):
        self.next_valid_req_id_ready.wait()
        current_id = self.req_id_counter
        self.req_id_counter += 1
        return current_id

    def historicalData(self, reqId: int, bar: BarData):
        t_str_original = bar.date
        t_parts = bar.date.split(' ')
        processed_datetime_utc = None

        if len(t_parts) > 1 and ':' in t_parts[1]: # Check if time component exists
            datetime_str_component = t_parts[0] + ' ' + t_parts[1]
            try:
                naive_datetime = datetime.datetime.strptime(datetime_str_component, '%Y%m%d %H:%M:%S')
                if len(t_parts) == 3:
                    ibkr_timezone_str = t_parts[2]
                    try:
                        ibkr_timezone = pytz.timezone(ibkr_timezone_str)
                        localized_datetime = ibkr_timezone.localize(naive_datetime)
                        processed_datetime_utc = localized_datetime.astimezone(pytz.utc)
                    except pytz.exceptions.UnknownTimeZoneError:
                        localized_datetime = self.expected_timezone.localize(naive_datetime)
                        processed_datetime_utc = localized_datetime.astimezone(pytz.utc)
                else:
                    localized_datetime = self.expected_timezone.localize(naive_datetime)
                    processed_datetime_utc = localized_datetime.astimezone(pytz.utc)
            except ValueError as e:
                print(f"Error parsing datetime string '{datetime_str_component}' from '{t_str_original}': {e}. Skipping bar.")
                return
        else: # Likely only date, e.g., for daily bars if formatDate was different
            try:
                # If it's just a date, assume it's for the whole day in expected_timezone
                naive_datetime = datetime.datetime.strptime(t_parts[0], '%Y%m%d')
                localized_datetime = self.expected_timezone.localize(naive_datetime) # Localize as start of day
                processed_datetime_utc = localized_datetime.astimezone(pytz.utc)
            except ValueError as e:
                print(f"Error parsing date string '{t_parts[0]}' from '{t_str_original}': {e}. Skipping bar.")
                return

        if processed_datetime_utc is None:
            print(f"Could not process timestamp: {t_str_original}. Skipping bar.")
            return

        if reqId in bar_collection:
            data = {
                'date': processed_datetime_utc,
                'open': bar.open,
                'high': bar.high,
                'low': bar.low,
                'close': bar.close,
                'volume': int(bar.volume)
            }
            bar_collection[reqId]['data'].append(data)
        else:
            print(f"Warning: Received historicalData for unknown reqId: {reqId}. Bar ignored.")

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        if reqId not in bar_collection:
            print(f"Warning: historicalDataEnd received for unknown reqId: {reqId}. No action taken.")
            return

        if not bar_collection[reqId]['data']:
            print(f"No data received for ReqId {reqId} (Start: {start}, End: {end}). Marking as complete.")
            bar_collection[reqId]['status'] = "complete"
            return

        df = pd.DataFrame(bar_collection[reqId]['data'])

        if df.empty:
            bar_collection[reqId]['status'] = "complete"
            return

        if 'date' not in df.columns:
            print(f"Error: 'date' column missing in DataFrame for ReqId: {reqId}. Marking as failed_permanent.")
            bar_collection[reqId]['status'] = "failed_permanent"
            bar_collection[reqId]['last_error_code'] = "INTERNAL_DATA_FORMAT_ERROR"
            return

        df['date'] = pd.to_datetime(df['date'], utc=True)
        df = df.sort_values('date')
        df.drop_duplicates(subset=['date'], keep='first', inplace=True)
        df['date'] = df['date'].dt.strftime('%Y-%m-%d %H:%M:%S')

        file_exists = os.path.exists(self.filename)
        write_header = not file_exists or os.path.getsize(self.filename) == 0

        try:
            df.to_csv(self.filename, mode='a', header=write_header, index=False)
            print(f"Data for ReqId {reqId} (target end {bar_collection[reqId]['end_date_obj_for_req'].strftime('%Y-%m-%d')}) successfully written to {self.filename}")
        except Exception as e:
            print(f"Error writing to CSV for ReqId {reqId}: {e}. Marking for retry.")
            bar_collection[reqId]['status'] = "error_retry"
            bar_collection[reqId]['last_error_code'] = CSV_WRITE_ERROR_CODE
            if 'retry_count' not in bar_collection[reqId]:
                bar_collection[reqId]['retry_count'] = 0
            return

        bar_collection[reqId]['data'] = []
        bar_collection[reqId]['status'] = "complete"

    def error(self, reqId: int, errorCode: int, errorString: str, advancedOrderRejectJson: str = ""):
        adv_reject_str = advancedOrderRejectJson if advancedOrderRejectJson else 'None'
        log_msg = f"Error. ReqId: {reqId}, Code: {errorCode}, Msg: {errorString}, Advanced: {adv_reject_str}"

        if errorCode in INFORMATIONAL_API_CODES:
            # Log informational codes, especially 2174 and 10314, which we are trying to handle
            print(f"Informational/Warning (Code: {errorCode}): {errorString}")
            if errorCode == 10314 and reqId != -1 and reqId in bar_collection: # If 10314 for a specific request
                 print(f"Error 10314 for ReqId {reqId} (End Date/Time invalid). Marking as failed_permanent despite UTC format attempt.")
                 bar_collection[reqId]['status'] = "failed_permanent"
                 bar_collection[reqId]['last_error_code'] = errorCode
            return

        print(log_msg) # Print other errors

        if errorCode in RETRYABLE_ERROR_CODES:
            if not self.connection_lost:
                print(f"True connection error detected (Code: {errorCode}). Setting connection_lost = True.")
                self.connection_lost = True
                self.is_connected_event.clear()
                self.next_valid_req_id_ready.clear()
            else:
                print(f"Connection error (Code: {errorCode}) received while already in connection_lost state.")
            return

        if reqId != -1 and reqId in bar_collection:
            if errorCode in REQUEST_SPECIFIC_RETRYABLE_ERROR_CODES:
                print(f"Request-specific retryable error for ReqId {reqId} (Code: {errorCode}). Marking for retry.")
                bar_collection[reqId]['status'] = "error_retry"
                bar_collection[reqId]['last_error_code'] = errorCode
                if 'retry_count' not in bar_collection[reqId]:
                    bar_collection[reqId]['retry_count'] = 0
            elif errorCode == 200: # No security definition
                 print(f"Error 200 for ReqId {reqId}: No security definition. Marking as failed_permanent.")
                 bar_collection[reqId]['status'] = "failed_permanent"
                 bar_collection[reqId]['last_error_code'] = errorCode
            else: # Unhandled error for a specific request
                print(f"Unhandled error for active ReqId {reqId} (Code: {errorCode}). Marking as failed_permanent.")
                bar_collection[reqId]['status'] = "failed_permanent"
                bar_collection[reqId]['last_error_code'] = errorCode
            return

        if reqId == -1: # General errors not tied to a request
            print(f"General system message (Code: {errorCode}). Not a connection error, informational, or request-specific.")

def load_config(config_path="config.json"):
    global config
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        print(f"Configuration loaded from {config_path}")
    except FileNotFoundError:
        print(f"Error: Configuration file {config_path} not found. Exiting.")
        exit(1)
    # ... (other exception handling for config loading)

def request_historical_data(app, contract_obj, end_date_time_api_str, req_id):
    """
    Requests historical data.
    end_date_time_api_str: Must be in format "YYYYMMDD-HH:MM:SS" (UTC) as per IBKR for this attempt.
    """
    print(f"Requesting historical data for ReqId: {req_id}, Contract: {contract_obj.symbol}, API EndDateTime (UTC): '{end_date_time_api_str}'")
    app.reqHistoricalData(reqId=req_id,
                          contract=contract_obj,
                          endDateTime=end_date_time_api_str,
                          durationStr="1 W",
                          barSizeSetting="1 min",
                          whatToShow="TRADES",
                          useRTH=1,
                          formatDate=1, # yyyyMMdd{space}HH:mm:ss (TZ is optional for return, we parse with expected_timezone)
                          keepUpToDate=False,
                          chartOptions=[])

def main():
    global config
    load_config()

    contract_details = config.get("contract", {})
    date_range_cfg = config.get("date_range", {})
    api_cfg = config.get("api_settings", {})
    output_cfg = config.get("output", {})
    tz_cfg = config.get("timezone_overrides", {})

    IBKR_PORT = api_cfg.get("ibkr_port", 7497)
    CLIENT_ID = api_cfg.get("client_id", 1)
    MAX_ACTIVE_REQUESTS = api_cfg.get("max_active_requests", 5)
    INTER_REQUEST_DELAY = api_cfg.get("inter_request_delay_seconds", 3) # Slightly increased
    MAX_CONNECT_RETRIES = api_cfg.get("max_connect_retries", 5)
    CONNECT_RETRY_DELAY = api_cfg.get("connect_retry_delay_seconds", 30)
    MAX_REQUEST_RETRIES = api_cfg.get("max_request_retries", 3)
    REQUEST_RETRY_DELAY = api_cfg.get("request_retry_delay_seconds", 20) # Slightly increased

    contract = Contract()
    contract.symbol = contract_details.get("symbol", "DEFAULT_SYMBOL")
    contract.secType = contract_details.get("secType", "STK")
    contract.exchange = contract_details.get("exchange", "SMART")
    contract.currency = contract_details.get("currency", "USD")
    primary_exchange_val = contract_details.get("primaryExchange")
    if primary_exchange_val:
        contract.primaryExchange = primary_exchange_val

    if contract.symbol == "DEFAULT_SYMBOL":
        print("Error: Contract symbol not defined in config.json. Exiting.")
        return

    today = datetime.date.today()
    default_start_year = today.year - 1 if today.month == 1 and today.day == 1 else today.year
    overall_start_date = datetime.date(
        date_range_cfg.get("start_year", default_start_year),
        date_range_cfg.get("start_month", 1),
        date_range_cfg.get("start_day", 1)
    )
    overall_end_date = datetime.date(
        date_range_cfg.get("end_year", today.year),
        date_range_cfg.get("end_month", today.month),
        date_range_cfg.get("end_day", today.day)
    )

    filename_template = output_cfg.get("filename_template", "{symbol}_1min_{start_year}_{end_year}_utc.csv")
    filename = filename_template.format(
        symbol=contract.symbol,
        start_year=overall_start_date.year,
        end_year=overall_end_date.year
    )
    print(f"Data will be saved to: {filename} (timestamps in UTC)")

    current_run_start_date = overall_start_date
    # ... (Resume logic - remains the same, assumes CSV dates are UTC parsable) ...
    if os.path.exists(filename):
        try:
            if os.path.getsize(filename) > 0:
                df_all_dates = pd.read_csv(filename, usecols=['date'], parse_dates=['date'], infer_datetime_format=True)
                if not df_all_dates.empty:
                    df_all_dates['date'] = pd.to_datetime(df_all_dates['date'], utc=True)
                    last_recorded_datetime_utc = df_all_dates['date'].max()
                    if pd.notna(last_recorded_datetime_utc):
                        resume_from_date = last_recorded_datetime_utc.date() + datetime.timedelta(days=1)
                        if resume_from_date > current_run_start_date:
                            print(f"Resuming download. Last data in {filename} up to {last_recorded_datetime_utc.date()}.")
                            current_run_start_date = resume_from_date
                            print(f"New effective start date for this run: {current_run_start_date}")
        # ... (exception handling for resume logic) ...
            else: print(f"{filename} exists but is empty. Using original start_date: {current_run_start_date}")
        except pd.errors.EmptyDataError: print(f"Warning: {filename} is empty. Using original start_date: {current_run_start_date}")
        except KeyError: print(f"Warning: 'date' column not found in {filename}. Cannot resume. Using original start_date.")
        except Exception as e: print(f"Warning: Error reading existing {filename} for resume: {e}. Using original start_date.")


    if current_run_start_date > overall_end_date:
        print(f"Effective start date {current_run_start_date} is after overall end date {overall_end_date}. No new data to download.")
        return

    exchange_to_timezone_map = tz_cfg.get("exchange_map", {})
    currency_to_timezone_map = tz_cfg.get("currency_map", {})
    default_fallback_tz = tz_cfg.get("default_fallback", "America/New_York")
    determined_timezone_str = default_fallback_tz
    # ... (timezone determination logic - remains the same) ...
    if contract.exchange and contract.exchange.upper() in exchange_to_timezone_map:
        determined_timezone_str = exchange_to_timezone_map[contract.exchange.upper()]
    elif contract.primaryExchange and contract.primaryExchange.upper() in exchange_to_timezone_map:
         determined_timezone_str = exchange_to_timezone_map[contract.primaryExchange.upper()]
    elif contract.currency and contract.currency.upper() in currency_to_timezone_map:
        determined_timezone_str = currency_to_timezone_map[contract.currency.upper()]
    print(f"Determined instrument's primary timezone for internal processing: {determined_timezone_str}")


    app = IBapi(filename=filename, expected_timezone_str=determined_timezone_str)
    api_thread = None
    # app.expected_timezone is the pytz object for the instrument's timezone

    # ... (Connection logic - remains the same) ...
    connected_successfully = False
    for attempt in range(MAX_CONNECT_RETRIES):
        print(f"Attempting to connect to IBKR (Port: {IBKR_PORT}, ClientID: {CLIENT_ID}) (Attempt {attempt + 1}/{MAX_CONNECT_RETRIES})...")
        # ... (rest of connection attempt) ...
        app.is_connected_event.clear(); app.next_valid_req_id_ready.clear(); app.connection_lost = False
        if app.isConnected(): app.disconnect(); time.sleep(0.1) # Short pause after disconnect
        if api_thread and api_thread.is_alive(): api_thread.join(timeout=5)
        
        app.connect('127.0.0.1', IBKR_PORT, clientId=CLIENT_ID)
        api_thread = threading.Thread(target=app.run, daemon=True, name="IBAPIThread")
        api_thread.start()

        if app.is_connected_event.wait(timeout=15) and app.next_valid_req_id_ready.wait(timeout=10):
            print("Successfully connected and nextValidId received.")
            connected_successfully = True; break
        else:
            print("Failed to connect or receive nextValidId within timeout.")
            if app.isConnected(): app.disconnect()
            if api_thread and api_thread.is_alive(): api_thread.join(timeout=5)
            if attempt < MAX_CONNECT_RETRIES - 1: print(f"Retrying connection in {CONNECT_RETRY_DELAY} seconds..."); time.sleep(CONNECT_RETRY_DELAY)
            else: print("Max connection retries reached. Exiting."); return
    if not connected_successfully: return


    tasks_to_process = []
    temp_date_iterator = overall_end_date
    while temp_date_iterator >= current_run_start_date:
        tasks_to_process.append(temp_date_iterator)
        temp_date_iterator -= datetime.timedelta(days=7)
    tasks_to_process.reverse()
    if not tasks_to_process and current_run_start_date <= overall_end_date:
        tasks_to_process.append(overall_end_date)
    print(f"Generated {len(tasks_to_process)} weekly tasks from {current_run_start_date} to {overall_end_date}.")

    while True:
        if app.connection_lost:
            # ... (Reconnection logic - remains the same) ...
            print("Connection lost. Attempting to reconnect...")
            if app.isConnected(): app.disconnect(); time.sleep(0.1)
            if api_thread and api_thread.is_alive(): api_thread.join(timeout=10)
            reconnected = False
            for rec_attempt in range(MAX_CONNECT_RETRIES):
                print(f"Reconnect attempt {rec_attempt + 1}/{MAX_CONNECT_RETRIES}...")
                app.is_connected_event.clear(); app.next_valid_req_id_ready.clear(); app.connection_lost = False
                app.connect('127.0.0.1', IBKR_PORT, clientId=CLIENT_ID)
                api_thread = threading.Thread(target=app.run, daemon=True, name="IBAPIThread-Reconnect")
                api_thread.start()
                if app.is_connected_event.wait(timeout=15) and app.next_valid_req_id_ready.wait(timeout=10):
                    print("Successfully reconnected."); reconnected = True; break
                else:
                    print("Reconnect attempt failed.")
                    if app.isConnected(): app.disconnect()
                    if api_thread and api_thread.is_alive(): api_thread.join(timeout=10)
                    if rec_attempt < MAX_CONNECT_RETRIES - 1: time.sleep(CONNECT_RETRY_DELAY)
            if not reconnected: print("Failed to reconnect. Exiting processing loop."); break


        active_requests_count = sum(1 for req_info in bar_collection.values() if req_info['status'] == "incomplete")

        for r_id in list(bar_collection.keys()):
            if bar_collection[r_id]['status'] == "error_retry":
                if active_requests_count < MAX_ACTIVE_REQUESTS:
                    if bar_collection[r_id]['retry_count'] < MAX_REQUEST_RETRIES:
                        bar_collection[r_id]['retry_count'] += 1
                        error_code = bar_collection[r_id].get('last_error_code', 'N/A')
                        original_end_date_obj = bar_collection[r_id]['end_date_obj_for_req']

                        print(f"Retrying request {r_id} (Attempt {bar_collection[r_id]['retry_count']}/{MAX_REQUEST_RETRIES}), Target EndDate: {original_end_date_obj.strftime('%Y-%m-%d')}, LastError: {error_code}")
                        bar_collection[r_id]['status'] = "incomplete"

                        # --- Convert target local end time to UTC string for API (RETRY) ---
                        naive_dt_local_retry = datetime.datetime.combine(original_end_date_obj, datetime.time(23, 59, 59))
                        localized_dt_local_retry = app.expected_timezone.localize(naive_dt_local_retry)
                        dt_utc_retry = localized_dt_local_retry.astimezone(pytz.utc)
                        end_date_str_for_api_retry_utc = dt_utc_retry.strftime("%Y%m%d-%H:%M:%S")
                        # --- End UTC conversion ---

                        if error_code == CSV_WRITE_ERROR_CODE:
                            # ... (CSV write retry logic - if fails, re-fetch using UTC string) ...
                            print(f"Attempting to re-write data for ReqId {r_id} due to previous CSV_WRITE_ERROR.")
                            if bar_collection[r_id]['data']:
                                # (Try writing to CSV again - code for this is omitted for brevity but would be here)
                                # If CSV write still fails:
                                print(f"Still failed to write CSV for ReqId {r_id} on retry. Will re-fetch data.")
                            bar_collection[r_id]['data'] = [] # Clear data for re-fetch
                            request_historical_data(app, contract, end_date_str_for_api_retry_utc, r_id)
                            active_requests_count += 1
                            time.sleep(REQUEST_RETRY_DELAY)
                        else: # Not a CSV write error, so re-fetch data from API
                            bar_collection[r_id]['data'] = []
                            request_historical_data(app, contract, end_date_str_for_api_retry_utc, r_id)
                            active_requests_count += 1
                            time.sleep(REQUEST_RETRY_DELAY)
                    else:
                        # ... (Max retries reached) ...
                        print(f"Max retries for ReqId {r_id}. Marking as failed_permanent.")
                        bar_collection[r_id]['status'] = "failed_permanent"
                else: break # Max active requests reached

        active_requests_count = sum(1 for req_info in bar_collection.values() if req_info['status'] == "incomplete")

        while tasks_to_process and active_requests_count < MAX_ACTIVE_REQUESTS:
            date_to_fetch_end = tasks_to_process.pop(0) # datetime.date object

            # --- Convert target local end time to UTC string for API (NEW REQUEST) ---
            naive_dt_local_new = datetime.datetime.combine(date_to_fetch_end, datetime.time(23, 59, 59))
            localized_dt_local_new = app.expected_timezone.localize(naive_dt_local_new) # Use instrument's timezone
            dt_utc_new = localized_dt_local_new.astimezone(pytz.utc)
            end_date_str_req_for_api_utc = dt_utc_new.strftime("%Y%m%d-%H:%M:%S") # Format YYYYMMDD-HH:MM:SS
            # --- End UTC conversion ---

            current_req_id = app.get_next_req_id()
            # Print API string for debugging
            print(f"Preparing new request for target end date: {date_to_fetch_end.strftime('%Y-%m-%d')}, ReqId: {current_req_id}")
            
            bar_collection[current_req_id] = {
                'data': [], 'status': "incomplete",
                'end_date_obj_for_req': date_to_fetch_end,
                'retry_count': 0
            }
            request_historical_data(app, contract, end_date_str_req_for_api_utc, current_req_id)
            active_requests_count += 1
            time.sleep(INTER_REQUEST_DELAY)

        # ... (Loop termination logic - remains the same) ...
        all_tasks_initiated = not tasks_to_process
        all_requests_settled = True
        pending_requests = False
        if not bar_collection and all_tasks_initiated: print("All tasks processed or no tasks generated."); break
        for req_id_check, req_status_info in bar_collection.items():
            if req_status_info['status'] in ["incomplete", "error_retry"]:
                all_requests_settled = False; pending_requests = True; break
        if all_tasks_initiated and all_requests_settled: print("All tasks processed and requests settled."); break
        if not pending_requests and not tasks_to_process: print("No pending requests and no tasks. Exiting."); break
        time.sleep(1)


    # ... (Final summary - remains the same) ...
    if bar_collection:
        completed_count = sum(1 for r in bar_collection.values() if r['status'] == "complete")
        failed_count = sum(1 for r in bar_collection.values() if r['status'] == "failed_permanent")
        print(f"Processing finished. Total initiated: {len(bar_collection)}. Completed: {completed_count}. Failed: {failed_count}.")
        if failed_count > 0:
            print("  Failed request details (target end dates):")
            for r_id, info in bar_collection.items():
                if info['status'] == 'failed_permanent':
                    print(f"    ReqId: {r_id}, Target EndDate: {info['end_date_obj_for_req'].strftime('%Y-%m-%d')}, Error: {info.get('last_error_code', 'N/A')}")

    print(f"Final data available in {filename}")
    if app.isConnected(): print("Disconnecting from IBKR..."); app.disconnect()
    if api_thread and api_thread.is_alive():
        print("Waiting for API thread to terminate...")
        api_thread.join(timeout=10)
        if api_thread.is_alive(): print("Warning: API thread did not terminate gracefully.")
    print("Script finished.")

if __name__ == "__main__":
    main()
