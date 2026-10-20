import os
import json

master_list = {}

legal_sets = [
    "lorcana_set01", # Set 001 - The First Chapter
    "lorcana_set02", # Set 002 - Rise of the Floodborn
    "lorcana_set03", # Set 003 - Into the Inklands
    "lorcana_set04", # Set 004 - Ursula's Return
    "lorcana_set05", # Set 005 - Shimmering Skies
    "lorcana_set06", # Set 006 - Azurite Sea
    "lorcana_set07", # Set 007 - Archazia's Island
    "lorcana_set08", # Set 008 - Reign of Jafar
    "lorcana_set09", # Set 009 - Fabled
    "lorcana_set10", # Set 010 - Whispers in the Well
    "lorcana_set11", # Set 011 - Winterspell
    "lorcana_set12", # Set 012 - Wilds Unknown
    "lorcana_set13", # Set 013 - Attack of the Vine!
    "lorcana_set14" # Set 014 - Hyperia City
]

# Path to the directory containing the JSON files
card_lists_dir = '.'

# Iterate through each file in the directory
for filename in os.listdir(card_lists_dir):
    if filename in [str(s) + '-cards.json' for s in legal_sets]:
        file_path = os.path.join(card_lists_dir, filename)
        try:
            with open(file_path, 'r') as f:
                # Load the dictionary from the current JSON file
                data = json.load(f)
                # Update master_list, overwriting duplicates
                master_list.update(data)
        except json.JSONDecodeError:
            print(f"Error: Could not decode JSON from file: {filename}")
        except Exception as e:
            print(f"An error occurred while processing {filename}: {e}")

# Save the combined master list to a new JSON file
output_filename = os.path.join(card_lists_dir, 'master_cardlist.json')
with open(output_filename, 'w') as f:
    json.dump(master_list, f, indent=2)

print(f"All JSON files combined into '{output_filename}'.")
print(f"Total unique cards: {len(master_list)}")
