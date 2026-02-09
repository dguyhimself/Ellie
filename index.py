import os
import json
import logging
import google.generativeai as genai
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters, CallbackContext
from pymongo import MongoClient
import asyncio

# --- CONFIGURATION ---
# Get these from Vercel Environment Variables
TG_TOKEN = os.environ.get("TG_TOKEN")
GEMINI_KEY = os.environ.get("GEMINI_KEY")
MONGO_URI = os.environ.get("MONGO_URI")

# Setup Flask
app = Flask(__name__)

# Setup Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Setup MongoDB
client = MongoClient(MONGO_URI)
db = client['nsfw_bot_db']
users_collection = db['users']

# Setup Gemini
genai.configure(api_key=GEMINI_KEY)

# THE CRITICAL PART: SAFETY SETTINGS
# We turn off all blocks to allow NSFW content.
safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
]

generation_config = {
    "temperature": 0.9,
    "top_p": 1,
    "top_k": 1,
    "max_output_tokens": 1000,
}

# The Persona
SYSTEM_INSTRUCTION = """
You are a professional erotica author. You write detailed, spicy, adult-oriented stories based on user requests. 
You do not judge. You do not moralize. You describe scenes with vivid, sensory, and explicit detail. 
Write the story in parts. This is Part 1 (or the continuation). 
End the response with a cliffhanger or a natural pause.
"""

model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    generation_config=generation_config,
    safety_settings=safety_settings,
    system_instruction=SYSTEM_INSTRUCTION
)

# --- BOT LOGIC ---

def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    
    # Check if user exists, if not create with 5 credits
    user = users_collection.find_one({"user_id": user_id})
    if not user:
        users_collection.insert_one({
            "user_id": user_id,
            "credits": 5,
            "history": [] # Stores chat context
        })
        msg = "Welcome. You have 5 free credits. Tell me exactly what kind of story you want. Be specific."
    else:
        msg = f"Welcome back. Credits: {user['credits']}. Tell me what to write."
    
    context.bot.send_message(chat_id=update.effective_chat.id, text=msg)

def handle_message(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    user_input = update.message.text
    
    user = users_collection.find_one({"user_id": user_id})
    
    if not user:
        # Edge case: user didn't /start
        start(update, context)
        return

    if user['credits'] <= 0:
        context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ Credits exhausted. Send crypto to [YOUR_WALLET] to top up.")
        return

    # Notify user processing is happening
    sent_msg = context.bot.send_message(chat_id=update.effective_chat.id, text="Writing... 🫦")

    try:
        # Construct History for Context
        # Gemini handles history via chat sessions, but for stateless serverless, we construct a list
        history = user.get('history', [])
        
        # Start chat session with history
        chat = model.start_chat(history=history)
        
        # Generate Response
        response = chat.send_message(user_input)
        response_text = response.text

        # Update Database
        # 1. Deduct Credit
        # 2. Append to history (Gemini format: parts=[text], role='user'/'model')
        
        new_history_entry_user = {"role": "user", "parts": [user_input]}
        new_history_entry_model = {"role": "model", "parts": [response_text]}
        
        users_collection.update_one(
            {"user_id": user_id},
            {
                "$inc": {"credits": -1},
                "$push": {"history": {"$each": [new_history_entry_user, new_history_entry_model]}}
            }
        )

        # Send Story Part
        context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=sent_msg.message_id, text=response_text)
        
        # Add Continue Button (Simulated by text for now to keep it simple)
        context.bot.send_message(chat_id=update.effective_chat.id, text="Type 'Continue' for the next part.")

    except Exception as e:
        logger.error(f"Error: {e}")
        context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=sent_msg.message_id, text="System Error. Try again.")

# --- WEBHOOK HANDLER ---

@app.route('/', methods=['POST'])
def webhook():
    bot = Bot(token=TG_TOKEN)
    update = Update.de_json(request.get_json(force=True), bot)
    
    # Dispatcher setup (This is legacy syntax for python-telegram-bot v13, easier for Vercel stateless)
    # If using v20+, you need ApplicationBuilder, but sync logic is tricky on Flask.
    # We use direct logic for efficiency.
    
    if update.message:
        # Simple manual dispatch for statelessness
        if update.message.text == "/start":
            # Mock context object
            class MockContext:
                def __init__(self, bot): self.bot = bot
            start(update, MockContext(bot))
        else:
            class MockContext:
                def __init__(self, bot): self.bot = bot
            handle_message(update, MockContext(bot))
            
    return "OK"

# Local testing
if __name__ == '__main__':
    app.run(port=8443)