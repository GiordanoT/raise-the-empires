import os
import xml.etree.ElementTree as ET
import urllib.request
import urllib.parse
import json
import re
import time
import shutil

# Files paths
xml_source_path = r"assets/game_configs/en_us.xml"      # English source (input)
xml_output_path = r"assets/game_configs/it_it.xml"      # Italian translation (output)
cache_path = r"assets/game_configs/translation_cache.json"

# Load translation cache
cache = {}
if os.path.exists(cache_path):
    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            cache = json.load(f)
        print(f"Loaded {len(cache)} cached translations.")
    except Exception as e:
        print(f"Error loading cache: {e}")

def save_cache():
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving cache: {e}")

def translate_batch(texts, target_lang='it', source_lang='en'):
    if not texts:
        return []
    
    # Filter out empty or already translated strings
    non_empty_indices = [i for i, t in enumerate(texts) if t.strip()]
    if not non_empty_indices:
        return texts
    
    non_empty_texts = [texts[i] for i in non_empty_indices]
    
    # We will use " \n " as a separator that Google Translate preserves
    separator = " \n "
    combined_text = separator.join(non_empty_texts)
    
    url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=" + source_lang + "&tl=" + target_lang + "&dt=t&q=" + urllib.parse.quote(combined_text)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    
    try:
        with urllib.request.urlopen(req) as response:
            res = json.loads(response.read().decode('utf-8'))
            translated_combined = "".join([seq[0] for seq in res[0] if seq[0]])
            
            # Split back
            parts = [p.strip() for p in translated_combined.split("\n")]
            if len(parts) == len(non_empty_texts):
                result = list(texts)
                for idx, val in zip(non_empty_indices, parts):
                    result[idx] = val
                return result
            else:
                result = list(texts)
                for idx in non_empty_indices:
                    result[idx] = translate_individual(texts[idx], target_lang, source_lang)
                    time.sleep(0.05)
                return result
    except Exception as e:
        result = list(texts)
        for idx in non_empty_indices:
            result[idx] = translate_individual(texts[idx], target_lang, source_lang)
            time.sleep(0.05)
        return result

def translate_individual(text, target_lang='it', source_lang='en'):
    if not text or text.strip() == "":
        return text
    url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=" + source_lang + "&tl=" + target_lang + "&dt=t&q=" + urllib.parse.quote(text)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req) as response:
            res = json.loads(response.read().decode('utf-8'))
            return "".join([seq[0] for seq in res[0] if seq[0]]).strip()
    except Exception as e:
        return text

# Regex to preserve Flash variables like {part,object,plural}
var_pattern = re.compile(r"\{[^}]+\}")

def translate_list(texts):
    if not texts:
        return []
    
    processed_texts = []
    variables_maps = []
    
    # Phase 1: Check cache and extract variables
    to_translate_texts = []
    to_translate_indices = []
    
    results = [None] * len(texts)
    
    for idx, text in enumerate(texts):
        if not text or text.strip() == "":
            results[idx] = text
            continue
            
        # Check cache first
        if text in cache:
            results[idx] = cache[text]
            continue
            
        # Extract variables
        variables = var_pattern.findall(text)
        variables_maps.append((idx, variables))
        
        # Replace variables with __VAR_X__
        temp_text = text
        for i, var in enumerate(variables):
            temp_text = temp_text.replace(var, f"__VAR_{i}__")
            
        to_translate_texts.append(temp_text)
        to_translate_indices.append(idx)
        
    # Phase 2: Translate in batches
    if to_translate_texts:
        batch_size = 30
        translated_processed = []
        for i in range(0, len(to_translate_texts), batch_size):
            batch = to_translate_texts[i:i+batch_size]
            translated_batch = translate_batch(batch)
            translated_processed.extend(translated_batch)
            time.sleep(0.2)
            
        # Phase 3: Restore variables and save to cache
        var_map_dict = {idx: vars for idx, vars in variables_maps}
        for local_idx, orig_idx in enumerate(to_translate_indices):
            trans_text = translated_processed[local_idx]
            restored = trans_text
            
            # Restore variables
            for j, var in enumerate(var_map_dict.get(orig_idx, [])):
                restored = restored.replace(f"__VAR_{j}__", var)
                restored = restored.replace(f"__var_{j}__", var)
                
            original_text = texts[orig_idx]
            cache[original_text] = restored
            results[orig_idx] = restored
            
        save_cache()
        
    return results

# Parse the English source XML
print("Parsing en_us.xml...")
tree = ET.parse(xml_source_path)
root = tree.getroot()

# List of packages sorted by priority
priority_packages = [
    "Main", "BuildingParts", "Combat", "Conversations", "Crews", 
    "DailyQuests", "Decorations", "Dialogs", "Footer", "Gifts", 
    "Languages", "Levels", "Objects", "Preloader", "Requests", 
    "Tooltips", "VisitorHelp"
]

all_packages = [pkg.attrib.get('name') for pkg in root]
other_packages = [p for p in all_packages if p not in priority_packages]

# Group packages for processing
packages_to_process = [p for p in priority_packages if p in all_packages] + other_packages

total_strings_processed = 0

for pkg_name in packages_to_process:
    pkg = root.find(f"./pkg[@name='{pkg_name}']")
    if pkg is None:
        continue
        
    strings = pkg.findall('string')
    if not strings:
        continue
        
    print(f"\nProcessing package: {pkg_name} ({len(strings)} strings)...")
    
    # Collect all items to translate from this package
    elements_to_translate = []
    original_texts = []
    
    for string_elem in strings:
        original_elem = string_elem.find('original')
        if original_elem is not None and original_elem.text:
            elements_to_translate.append(original_elem)
            original_texts.append(original_elem.text)
            
        for var_elem in string_elem.findall('variation'):
            if var_elem is not None and var_elem.text:
                elements_to_translate.append(var_elem)
                original_texts.append(var_elem.text)
                
    if not original_texts:
        continue
        
    # Translate
    translated_texts = translate_list(original_texts)
    
    # Apply back to XML
    for elem, trans_text in zip(elements_to_translate, translated_texts):
        elem.text = trans_text
        
    total_strings_processed += len(original_texts)
    print(f"Finished {pkg_name}. Total strings processed so far: {total_strings_processed}")
    
    # Save XML incrementally to the Italian output file
    tree.write(xml_output_path, encoding="UTF-8", xml_declaration=True)
    print(f"Saved progress to {xml_output_path}")

print("\nTranslation completed successfully!")
