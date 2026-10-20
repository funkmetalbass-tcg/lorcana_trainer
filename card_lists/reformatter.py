import json
import re

def format_description(text):
    if not text:
        return None
    
    # 1. Standardize explicit Lorcana symbols
    text = text.replace('{I}', 'Ink')
    text = text.replace('{S}', 'Strength')
    text = text.replace('{L}', 'Lore')
    text = text.replace('{W}', 'Willpower')
    text = text.replace('{E}', '[Exert]')
    
    # 2. Contextually infer empty {} symbols found in Set 13's raw text
    text = re.sub(r'pay (\d+)\s*\{\}', r'pay \1 Ink', text)
    text = re.sub(r'can\s*\{\}', r'can [Exert]', text)
# # These were Claude's suggestions. Not sure.    
#    text = re.sub(r'\+([0-9]+)\s*\{\}', r'+\1 Strength', text)
#    text = text.replace('{}', '[Exert]') # Catch-all fallback
    
    # 3. Standardize line breaks for HTML
    text = text.replace('\r\n', '\n').replace('\n', '<br>\n')
    
    # 4. Bold Ability Names (All caps phrases followed by standard sentence case)
    def bold_ability(match):
        ability_name = match.group(1).upper()
        return f"<strong>[{ability_name}]</strong> "

    # Matches uppercase phrases at the start of the string or after a line break
    text = re.sub(r'(?:^|<br>\n)([A-Z][A-Z0-9\s\'\!\?\,\.\-]*[A-Z0-9\!])\s+(?=[A-Z][a-z])', bold_ability, text)
    
    # 5. Italicize reminder text in parentheses
    text = re.sub(r'(\(.*?\))', r'<em>\1</em>', text)
    
    # 6. Bold common Keywords to match the target JSON style
    keywords = [
        "Alert",
        "Adventurous",
        "Bodyguard",
        "Boost",
        "Challenger",
        "Combo Shift",
        "Duo Shift",
        "Floodborn Shift",
        "Evasive",
        "Madrigal Shift",
        "Potato Shift",
        "Reckless",
        "Resist",
        "Rush",
        "Singer",
        "Support",
        "Sing Together",
        "Shift",
        "Temporary Red Panda Shift",
        "Temporary Shift",
        "Vanish",
        "Ward"
    ]
    for kw in keywords:
        text = re.sub(rf'\b({kw})\b', r'<strong>\1</strong>', text)

    return text

def transform_lorcana_data(input_file, output_file):
    with open(input_file, 'r', encoding='utf-8') as f:
        source_data = json.load(f)

    target_data = {}

    for card in source_data:       
        # Construct the unique dictionary key
        name = card.get('name', 'Unknown')
        version = card.get('version')
        
        if version:
            key = f"{name} - {version}"
        else:
            key = name
            
        target_card = {}
        
        # Format Rarity (e.g., "Super_rare" -> "Super Rare")
        rarity = card.get('rarity')
        if rarity:
            target_card["Rarity"] = rarity.replace('_', ' ').title()
        # Remove alternate art printings
        if rarity not in ["Common", "Uncommon", "Rare", "Super Rare", "Legendary"]:
            continue
            
        # Format Description text
        text = card.get('text')
        formatted_text = format_description(text)
        if formatted_text:
            target_card["Description"] = formatted_text
            
        # Property is missing from source data
#        target_card["Property"] = "Unknown"
        
        # Ink Cost
        if card.get('cost') is not None:
            target_card["Cost Ink"] = str(card.get('cost'))

        # Character Version
        if version:
            target_card["Character Version"] = version
            
        # Inkwell Icon (boolean to "Yes"/"No")
        target_card["InkwellIcononCard"] = "Yes" if card.get('inkwell') else "No"
        
        # Ink Type (Handle multiple inks for Set 13 Team cards)
        inks = card.get('inks')
        if inks and isinstance(inks, list):
            target_card["InkType"] = ";".join(inks)
        elif card.get('ink'):
            target_card["InkType"] = card.get('ink')
            
        # Card Type
        card_types = card.get('type', [])
        if card_types:
            target_card["CardType"] = card_types[0]
            
        # Classification (Merge primary classifications with secondary types like 'Song')
        classifications = card.get('classifications') or []
        if len(card_types) > 1:
            classifications.extend(card_types[1:])
            
        if classifications:
            # Filter out any nulls if they exist
            valid_classes = [c for c in classifications if c]
            target_card["Classification"] = ";".join(valid_classes)
            
        # Core Stats
        if card.get('strength') is not None:
            target_card["Strength"] = str(card.get('strength'))
        if card.get('willpower') is not None:
            target_card["Willpower"] = str(card.get('willpower'))
        if card.get('lore') is not None:
            target_card["Lore Value"] = str(card.get('lore'))
            
        # Location Specific Stat
        if card.get('move_cost') is not None:
            target_card["Move Cost"] = str(card.get('move_cost'))
            
        target_data[key] = target_card

    # Write output to new JSON
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(target_data, f, indent=2)

if __name__ == "__main__":
    # Ensure your source files are named 'lorcana_setXX.json' and are in the same directory
    for i in range(14):
        transform_lorcana_data(f"lorcana_set{i+1:02}.json", f"lorcana_set{i+1:02}-cards.json")
        print(f"Reformatting complete! Check 'lorcana_set{i+1:02}-cards.json'.")
