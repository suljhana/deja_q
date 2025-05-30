import os
import logging
from slack_sdk import WebClient
from typing import Optional
from .vector_store import MessageVectorStore
from .ollama_client import OllamaClient

class MessageHandler:
    def __init__(self, vector_store: MessageVectorStore):
        self.client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
        self.vector_store = vector_store
        self.similarity_threshold = 0.8
        self.ollama = OllamaClient(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=os.getenv("OLLAMA_MODEL", "mistral")
        )

    def handle_message(self, event_data: dict) -> None:
        """Handle incoming message events."""
        message = event_data["event"]
        
        # Log the incoming event
        logging.info(f"\n{'='*40} New Message Event {'='*40}")
        logging.info(f"Message ID: {message.get('client_msg_id', 'No client_msg_id')}")
        logging.info(f"Message TS: {message.get('ts', 'No ts')}")
        logging.info(f"Thread TS: {message.get('thread_ts', 'No thread_ts')}")
        logging.info(f"Event Type: {message.get('type', 'No type')}")
        logging.info(f"Subtype: {message.get('subtype', 'No subtype')}")
        logging.info(f"Bot ID: {message.get('bot_id', 'No bot_id')}")
        logging.info(f"App ID: {message.get('app_id', 'No app_id')}")
        logging.info(f"User: {message.get('user', 'No user')}")
        logging.info(f"Text: {message.get('text', 'No text')}")
        
        # Ignore messages that are:
        # - from bots/apps
        # - part of a thread (have a thread_ts)
        # - have no real user
        # - message_changed events
        if (message.get("subtype") is None and    # No subtype (regular message)
                not message.get("bot_id") and      # Not from a bot
                not message.get("app_id") and      # Not from an app
                not message.get("thread_ts") and   # Not a thread reply
                message.get("user")):              # Has a real user
            
            try:
                # Get channel info
                channel_info = self.client.conversations_info(channel=message["channel"])
                channel_name = channel_info["channel"]["name"]
                
                # Log channel info
                logging.info(f"Channel Name: {channel_name}")
                logging.info(f"Channel ID: {message['channel']}")
                
                # Check if this is the prototype channel
                if channel_name == self.vector_store.channel_name:
                    logging.info("Processing message in prototype channel")
                    self._process_message(message, message["channel"])
                else:
                    logging.info(f"Ignoring message - not in prototype channel (got {channel_name}, expected {self.vector_store.channel_name})")
            except Exception as e:
                logging.error(f"Error handling message: {str(e)}")
        else:
            # Log why message was ignored
            reasons = []
            if message.get("subtype") is not None:
                reasons.append(f"has subtype: {message.get('subtype')}")
            if message.get("bot_id"):
                reasons.append("from bot")
            if message.get("app_id"):
                reasons.append("from app")
            if message.get("thread_ts"):
                reasons.append("is thread reply")
            if not message.get("user"):
                reasons.append("no user")
            
            logging.info(f"Ignoring message because: {', '.join(reasons)}")
        
        logging.info(f"{'='*90}\n")

    def _process_message(self, message: dict, channel_id: str) -> None:
        """Process a message and find similar previous messages."""
        try:
            # First check for similar messages
            similar_messages = self.vector_store.find_similar_messages(
                message["text"],
                threshold=self.similarity_threshold
            )

            # Filter out the current message if it somehow got into the results
            similar_messages = [
                msg for msg in similar_messages 
                if msg["ts"] != message["ts"]  # Not the same message
            ]

            # Send appropriate response based on whether similar messages were found
            if similar_messages:
                # Get the most similar message
                best_match = similar_messages[0]
                similarity_percentage = best_match["similarity"] * 100
                
                # Get the thread messages for the best match
                thread_messages = self.vector_store.get_thread_messages(
                    channel_id,
                    best_match["ts"]
                )
                
                # Generate a summary of the thread
                if thread_messages:
                    summary = self.ollama.summarize_thread(
                        thread_messages,
                        thread_id=best_match["ts"]  # Pass the thread timestamp as identifier
                    )
                    
                    response = (
                        f"I found a similar question that was asked before! "
                        f"(Similarity: {similarity_percentage:.1f}%)\n"
                        f"You can find it here: {best_match['permalink']}\n\n"
                        f"Here's a summary of the previous answer:\n"
                        f"```\n{summary}\n```"
                    )
                else:
                    response = (
                        f"I found a similar question that was asked before! "
                        f"(Similarity: {similarity_percentage:.1f}%)\n"
                        f"You can find it here: {best_match['permalink']}"
                    )
                
                self.client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=message.get("ts"),  # Create new thread with original message
                    text=response
                )
            else:
                self.client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=message.get("ts"),  # Create new thread with original message
                    text="Sorry, I couldn't find any similar questions that have been asked before."
                )

            # After processing, add the new message to the vector store
            self.vector_store.add_message(message, channel_id)
            logging.info(f"Added new message to vector store: {message['text'][:50]}...")

        except Exception as e:
            logging.error(f"Error processing message: {str(e)}")
            # Send an error message to the user
            self.client.chat_postMessage(
                channel=channel_id,
                thread_ts=message.get("ts"),
                text="Sorry, I encountered an error while processing your message."
            ) 