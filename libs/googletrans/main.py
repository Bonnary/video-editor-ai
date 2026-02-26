from googletrans import Translator

def translate_text(text, target_language='en'):
    # Initialize the Translator
    translator = Translator()
    
    try:
        # Perform the translation
        # dest is the target language code (e.g., 'en' for English, 'es' for Spanish, 'fr' for French)
        result = translator.translate(text, dest=target_language)
        
        # Print the results
        print(f"Original Text: {text}")
        print(f"Detected Source Language: {result.src}")
        print(f"Translated Text ({result.dest}): {result.text}")
        
        return result.text
        
    except Exception as e:
        print(f"An error occurred: {e}")
        return None

# --- Example Usage ---
if __name__ == "__main__":
    
    # English text to translate to Khmer ('km')
    english_text = "Writing code is a lot of fun!"
    translate_text(english_text, target_language='km')  # Khmer language code is 'km'