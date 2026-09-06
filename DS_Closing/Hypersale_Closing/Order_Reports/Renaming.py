import os
import re

# --- USER CONFIGURATION ---
DIRECTORY_TO_SCAN = r"/Users/kamranbaloch/Downloads/Kamran Macos/Dropshipping/2. Hypersale/Order_Reports"
DRY_RUN = False  # Set to True to test, False to actually rename
# --- END CONFIGURATION ---

MONTH_MAP = {
    'jan': ('January', '01'), 'january': ('January', '01'),
    'feb': ('February', '02'), 'february': ('February', '02'),
    'mar': ('March', '03'), 'march': ('March', '03'),
    'apr': ('April', '04'), 'april': ('April', '04'),
    'may': ('May', '05'),
    'jun': ('June', '06'), 'june': ('June', '06'),
    'jul': ('July', '07'), 'july': ('July', '07'),
    'aug': ('August', '08'), 'august': ('August', '08'),
    'sep': ('September', '09'), 'september': ('September', '09'),
    'oct': ('October', '10'), 'october': ('October', '10'),
    'nov': ('November', '11'), 'november': ('November', '11'),
    'dec': ('December', '12'), 'december': ('December', '12'),
}

def rename_files():
    print(f"Scanning directory: {DIRECTORY_TO_SCAN}\n")
    if DRY_RUN:
        print("--- RUNNING IN DRY RUN MODE ---")
    print("-" * 30)

    try:
        filenames = os.listdir(DIRECTORY_TO_SCAN)
    except Exception as e:
        print(f"Error accessing directory: {e}")
        return

    renamed_count = 0
    already_correct_count = 0
    skipped_count = 0

    # Build the regex string for months
    month_regex = "|".join(MONTH_MAP.keys())

    # PATTERN 1: Month comes first (e.g., Nov-2025.txt, October 2024.txt)
    pattern_month_first = re.compile(rf"(?i)^({month_regex})[-_\s]+(\d{{4}}|\d{{2}})\.txt$")
    
    # PATTERN 2: Year comes first (e.g., 2024-Oct.txt, 2025_Nov.txt)
    pattern_year_first = re.compile(rf"(?i)^(\d{{4}}|\d{{2}})[-_\s]+({month_regex})\.txt$")

    # PATTERN 3: Already Correct (e.g., 2024-11-November.txt)
    pattern_correct = re.compile(r"^\d{4}-\d{2}-[A-Za-z]+\.txt$")

    for filename in filenames:
        if not filename.endswith('.txt'):
            continue

        # 1. Check if already correct
        if pattern_correct.match(filename):
            already_correct_count += 1
            continue  # Skip silently

        year_str = None
        month_str = None

        # 2. Check "Month First" (Nov-2025)
        match_m_first = pattern_month_first.match(filename)
        if match_m_first:
            month_str = match_m_first.group(1).lower()
            year_str = match_m_first.group(2)

        # 3. Check "Year First" (2024-Nov)
        if not year_str: # Only check if we haven't found a match yet
            match_y_first = pattern_year_first.match(filename)
            if match_y_first:
                year_str = match_y_first.group(1)
                month_str = match_y_first.group(2).lower()

        # If we found a valid pattern (either 2 or 3)
        if year_str and month_str:
            try:
                # Normalize Year
                if len(year_str) == 2:
                    full_year = "20" + year_str
                else:
                    full_year = year_str

                # Normalize Month
                month_name, month_num = MONTH_MAP[month_str]
                
                # Create New Name
                new_filename = f"{full_year}-{month_num}-{month_name}.txt"
                
                old_path = os.path.join(DIRECTORY_TO_SCAN, filename)
                new_path = os.path.join(DIRECTORY_TO_SCAN, new_filename)

                if DRY_RUN:
                    print(f"DRY RUN: '{filename}' -> '{new_filename}'")
                else:
                    os.rename(old_path, new_path)
                    print(f"SUCCESS: '{filename}' -> '{new_filename}'")
                
                renamed_count += 1

            except Exception as e:
                print(f"ERROR: Could not rename '{filename}': {e}")
                skipped_count += 1
        else:
            # Matches no known pattern
            print(f"INFO: Skipping '{filename}' (Unknown format)")
            skipped_count += 1

    print("-" * 30)
    if DRY_RUN:
        print(f"\nDRY RUN COMPLETE. {renamed_count} files would be renamed.")
    else:
        print(f"\nRenaming complete. {renamed_count} files renamed.")
    
    print(f"{already_correct_count} files were already correct.")
    print(f"{skipped_count} files were skipped (unknown format).")

if __name__ == "__main__":
    rename_files()