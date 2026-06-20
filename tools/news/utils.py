
import re

def clean_display_name(name: str) -> str:
    if not name:
        return "User"
        
    name = re.sub(r'^(?:cc\s*[\-\|\]\)\:]+|\[cc\]|\(cc\))\s*', '', name, flags=re.IGNORECASE)
    
    name = re.sub(r'[\s★ツ🥵❤✨👑💀🔥❗❓]+$', '', name)
    
    name = name.replace("|", " ")
    
    name = " ".join(name.split()).strip()
    
    return name if name else "User"