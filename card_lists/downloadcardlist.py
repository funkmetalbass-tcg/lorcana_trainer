import os
import requests
import json

#for i in {1..13}; do
#  padded=$(printf "%02d" "$i")
#  curl -s "https://api.lorcast.com/v0/sets/${i}/cards" -o ~/lorcast_set${padded}.json
#  echo "Downloaded set ${padded}"
#  sleep 0.1
#done


lorcana_sets = [
  22937, # Set 001 - The First Chapter
  23303, # Set 002 - Rise of the Floodborn
  23367, # Set 003 - Into the Inklands
  23474, # Set 004 - Ursula's Return
  23536, # Set 005 - Shimmering Skies
  23746, # Set 006 - Azurite Sea
  24011, # Set 007 - Archazia's Island
  24258, # Set 008 - Reign of Jafar
  24348, # Set 009 - Fabled
  24414, # Set 010 - Whispers in the Well
  24500, # Set 011 - Winterspell
  24617, # Set 012 - Wilds Unknown
  24666] # Set 013 - Attack of the Vine!
lorcana_sets.sort(reverse=False)
lorcana_sets = lorcana_sets
url_base = 'https://tcgcsv.com/tcgplayer/71/'

download_dir = '.'
os.makedirs(download_dir, exist_ok=True)

master_card_dict = {}
master_filename = os.path.join(download_dir, "master_card_list" + '.json')

for set_id in lorcana_sets:
  url = url_base + str(set_id) + '/products'
  filename = os.path.join(download_dir, str(set_id) + '.json')
  master_filename = os.path.join(download_dir, "master_card_list" + '.json')

  # Only download file if it exists
  if os.path.exists(filename):
    print(f"The file '{filename}' already exists locally.")
  else:
    print(f"The file '{filename}' does not exist locally. Downloading and extracting 'results'...")
    try:
      response = requests.get(url)
      response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
      full_data = response.json() # Parse the full response as JSON
      # Extract main dictionary from JSON file
      results_data = full_data.get('results', [])
      # Write each output to its own json file
      with open(filename, 'w') as f: # Open in write mode for text (JSON)
        json.dump(results_data, f, indent=2) # Save only the filtered_results_data as JSON
      print(f"Successfully downloaded and extracted filtered 'results' to '{filename}'.")
    except requests.exceptions.RequestException as e:
      print(f"Error downloading the file: {e}")
    except json.JSONDecodeError:
      print(f"Error: Could not decode JSON from the response for {set_id}.")
    except KeyError:
      print(f"Error: 'results' key not found in the downloaded data for {set_id}.")

      
# Parse dictionary in each JSON file, make a new dictionary
# with only the relevant data we care about
for set_id in lorcana_sets:
  filename = os.path.join(download_dir, str(set_id) + '.json')
  with open(filename, 'r') as f:
    result = json.load(f)
    if set_id in [24617,24666]:
      result = result['results']
    filtered_dict = {}
    for item_dict in result:
      cardName = item_dict["name"]
      # Remove cards with extra rarity; in the future this should be done by simply looking at CardRarity; may help with promos too
      if ("(Iconic)" not in cardName and
          "(Enchanted)" not in cardName and
          "(Epic)" not in cardName and
          len(item_dict["extendedData"]) > 0):
        new_item_dict = {}
        # # TCGPlayer Specific Stuff?
        # new_item_dict["cleanName"] = item_dict["cleanName"]
        # new_item_dict["productId"] = item_dict["productId"]
        # new_item_dict["url"] = item_dict["url"]
        for ed in item_dict["extendedData"]:
          k = ed["name"]
          v = ed["value"]
          if k not in ["Flavor Text","Number"]:  # Filter out flavor text
            new_item_dict[k] = v
        # Choose which cards to keep
        if( new_item_dict.get("InkType") is not None # This avoids puzzle cards
            #and new_item_dict.get("InkType") == "Steel"
           ):
          filtered_dict[cardName] = new_item_dict
          master_card_dict[cardName] = new_item_dict
    filtered_filename = os.path.join(download_dir, str(set_id) + "-cards.json")
    with open(filtered_filename, 'w') as f2:
      json.dump(filtered_dict, f2, indent=2)

with open(master_filename, 'w') as f3:
  json.dump(master_card_dict, f3, indent=2)
