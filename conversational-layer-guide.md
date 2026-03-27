# Adding a Conversational LLM Layer to the Elevate Foods Operations App

## Technical Implementation Guide for Brother

**Purpose:** This document provides a complete technical blueprint for adding a conversational AI interface to the existing Python/Anaconda inventory management app (Roboute-kun). The app already connects to Shopify, Recharge, and internal inventory/incoming data. This guide explains how to wire Claude's API with tool-use (function calling) so a user can ask plain-English questions and get actionable recommendations based on live business data.

---

## 1. Architecture Overview

### Current State
The app already has:
- Live connections to Shopify (orders, line items, SKU data)
- Recharge subscription data (subscriber counts, upcoming renewals)
- Internal inventory tracking (on-hand counts, incoming POs)
- Incoming shipment tracking
- Python/Anaconda environment on Windows

### Target State
Add a conversational layer where the user types a question, the LLM decides which data it needs, the app fetches that data, and the LLM interprets the results with actionable recommendations.

### Flow Diagram
```
User types question (plain English)
        │
        ▼
┌─────────────────────────────┐
│  Claude API (with tools)    │
│  Receives question + tool   │
│  definitions                │
│  Decides which tools to call│
└──────────┬──────────────────┘
           │ Returns tool_use blocks
           ▼
┌─────────────────────────────┐
│  Your Python App             │
│  Parses tool requests       │
│  Calls actual data functions│
│  (inventory, Shopify, etc.) │
└──────────┬──────────────────┘
           │ Returns tool results
           ▼
┌─────────────────────────────┐
│  Claude API (continuation)  │
│  Reads real data            │
│  Generates recommendation   │
└──────────┬──────────────────┘
           │
           ▼
   Answer displayed to user
```

### Why This Is NOT RAG
Traditional RAG uses vector databases and embeddings to search unstructured text. This app has **structured, queryable data** — inventory counts, order numbers, dates, SKUs. That means we use Claude's **tool-use / function-calling** feature instead, which is simpler, more accurate, and doesn't require a vector database.

---

## 2. Prerequisites and Setup

### Install the Anthropic Python SDK
```bash
# In Anaconda prompt or terminal
pip install anthropic
```

### API Key
You need a Claude API key from https://console.anthropic.com/. Store it as an environment variable:

**Windows (PowerShell):**
```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-your-key-here"
# Or set permanently in System Environment Variables
```

**Windows (CMD):**
```cmd
set ANTHROPIC_API_KEY=sk-ant-your-key-here
```

**Or in Python directly (less secure, fine for dev):**
```python
import anthropic
client = anthropic.Anthropic(api_key="sk-ant-your-key-here")
```

### Which Model to Use
- **claude-sonnet-4-6** — Best cost/performance for this use case. Fast, great at tool-use, cheap. Use this as default.
- **claude-opus-4-6** — Use only if Sonnet struggles with complex multi-step reasoning about inventory tradeoffs.

### Estimated API Cost
For an operational assistant queried 20-50 times/day with moderate data payloads, expect roughly $5-15/month on Sonnet. This is very cheap.

---

## 3. Define Your Tools (Functions)

Tools are the bridge between Claude and your data. Each tool is a Python function that already exists (or can be easily written) in the app. Claude doesn't call them directly — it tells you which tool it wants to use and with what parameters, your code executes it, and you send the result back.

### Tool Definition Format
Each tool needs:
1. A **name** (snake_case)
2. A **description** (tells Claude when to use it — be detailed)
3. An **input_schema** (JSON Schema describing the parameters)

### Recommended Tools for This App

```python
tools = [
    {
        "name": "get_inventory_levels",
        "description": "Get current on-hand inventory counts for one or more SKUs. Use this when the user asks about stock levels, what's available, or how much of something is in the warehouse. Returns SKU, product name, and quantity on hand.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skus": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of SKU codes to check. If empty or not provided, returns all SKUs."
                },
                "category": {
                    "type": "string",
                    "enum": ["cheese", "meat", "accompaniment", "packaging", "all"],
                    "description": "Filter by product category. Use prefix logic: CH- for cheese, MT- for meat, AC- for accompaniment, PK- for packaging."
                }
            },
            "required": []
        }
    },
    {
        "name": "get_incoming_shipments",
        "description": "Get expected incoming purchase orders and shipments. Use when the user asks about incoming stock, pending POs, what's on the way, or expected delivery dates. Returns PO number, SKU, quantity, expected date, and supplier.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_range_start": {
                    "type": "string",
                    "description": "Start date for incoming shipments (YYYY-MM-DD). Defaults to today."
                },
                "date_range_end": {
                    "type": "string",
                    "description": "End date for incoming shipments (YYYY-MM-DD). Defaults to 30 days from now."
                },
                "sku": {
                    "type": "string",
                    "description": "Optional specific SKU to filter by."
                }
            },
            "required": []
        }
    },
    {
        "name": "get_subscription_counts",
        "description": "Get Recharge subscription counts for upcoming ship waves. Use when the user asks how many subscribers, how many boxes are shipping, what the demand looks like for the next wave, or any wave-related planning questions. Returns counts broken down by box type/suffix (OWC, SPN, MDT, BYO, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "wave_date": {
                    "type": "string",
                    "description": "The ship wave date (YYYY-MM-DD). If not provided, returns the next upcoming wave."
                },
                "box_type": {
                    "type": "string",
                    "description": "Optional filter by box suffix: MONG, OWC, SPN, MDT, BYO, ALPN, SS, ISUN, HHIGH, MS, NMS"
                }
            },
            "required": []
        }
    },
    {
        "name": "get_shortfall_report",
        "description": "Calculate inventory shortfalls for an upcoming ship wave. Compares current inventory + expected incoming against projected demand from subscriptions. Use when the user asks what's short, what needs to be ordered, what they should be worried about, or for a pre-wave readiness check. Returns items that are short, how many units short, and whether incoming POs will cover the gap.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wave_date": {
                    "type": "string",
                    "description": "The ship wave date to analyze (YYYY-MM-DD). Defaults to next upcoming wave."
                }
            },
            "required": []
        }
    },
    {
        "name": "get_shopify_orders",
        "description": "Query recent Shopify orders. Use when the user asks about recent orders, order status, one-time purchases, or specific customer orders. Can filter by date range, fulfillment status, or financial status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["unfulfilled", "fulfilled", "any"],
                    "description": "Order fulfillment status filter."
                },
                "date_range_start": {
                    "type": "string",
                    "description": "Start date (YYYY-MM-DD)."
                },
                "date_range_end": {
                    "type": "string",
                    "description": "End date (YYYY-MM-DD)."
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of orders to return. Default 50."
                }
            },
            "required": []
        }
    },
    {
        "name": "get_shipping_status",
        "description": "Get shipping and tracking status for recent shipments. Use when the user asks about delivery status, transit times, carrier issues, or shipment tracking. Returns carrier, tracking status, ship date, and delivery date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wave_date": {
                    "type": "string",
                    "description": "Filter by ship wave date (YYYY-MM-DD)."
                },
                "carrier": {
                    "type": "string",
                    "enum": ["fedex", "ups", "lasership", "all"],
                    "description": "Filter by carrier."
                },
                "status": {
                    "type": "string",
                    "enum": ["in_transit", "delivered", "exception", "all"],
                    "description": "Filter by shipment status."
                }
            },
            "required": []
        }
    }
]
```

### Important: Tool Descriptions Are Critical
Claude decides which tool to call based on the description. Write descriptions as if you're explaining to a smart coworker when they should use this function. Include:
- What it returns
- When to use it
- What kind of user questions it answers

---

## 4. The Agentic Tool-Use Loop (Core Implementation)

This is the main engine. It handles the back-and-forth between Claude and your data functions.

### Option A: Manual Loop (Full Control, Recommended to Start)

```python
import anthropic
import json

client = anthropic.Anthropic()  # Uses ANTHROPIC_API_KEY env var

# Map tool names to your actual Python functions
def execute_tool(tool_name: str, tool_input: dict) -> str:
    """
    Route tool calls to your actual data functions.
    Each function should return a JSON-serializable dict or string.
    """
    if tool_name == "get_inventory_levels":
        result = your_app.get_inventory_levels(**tool_input)
    elif tool_name == "get_incoming_shipments":
        result = your_app.get_incoming_shipments(**tool_input)
    elif tool_name == "get_subscription_counts":
        result = your_app.get_subscription_counts(**tool_input)
    elif tool_name == "get_shortfall_report":
        result = your_app.get_shortfall_report(**tool_input)
    elif tool_name == "get_shopify_orders":
        result = your_app.get_shopify_orders(**tool_input)
    elif tool_name == "get_shipping_status":
        result = your_app.get_shipping_status(**tool_input)
    else:
        result = {"error": f"Unknown tool: {tool_name}"}
    
    # Always return a string (JSON-encoded if dict)
    if isinstance(result, dict) or isinstance(result, list):
        return json.dumps(result, default=str)
    return str(result)


def chat(user_message: str, conversation_history: list, tools: list) -> str:
    """
    Send a message through the agentic loop.
    Handles multi-turn tool use automatically.
    """
    # Add user message to history
    conversation_history.append({
        "role": "user",
        "content": user_message
    })
    
    # System prompt — this shapes how Claude behaves as your ops assistant
    system_prompt = """You are the operations assistant for Elevate Foods / AppyHour Box, 
a premium cheese and charcuterie subscription box service. You have access to tools 
that query live inventory, subscription, shipping, and order data.

When answering questions:
1. Always check the actual data using your tools before making claims.
2. If multiple tools are needed, call them all to get a complete picture.
3. Provide specific numbers, not vague statements.
4. When reporting shortfalls, also check incoming shipments to see if they cover the gap.
5. Proactively flag risks (e.g., items with no incoming PO to cover a shortfall).
6. Recommend specific actions: "Contact supplier X", "Move Y units from Z", etc.
7. Use the SKU naming conventions: CH- prefix is cheese, MT- is meat, AC- is accompaniment, PK- is packaging.
8. Box suffixes indicate flavor profiles: OWC, SPN, MDT, BYO, ALPN, SS, ISUN, HHIGH, MS, NMS.

Be direct and actionable. This is an operational tool, not a chatbot."""

    # The agentic loop
    while True:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=system_prompt,
            tools=tools,
            messages=conversation_history
        )
        
        # Check if Claude wants to use tools
        if response.stop_reason == "tool_use":
            # Add Claude's response (with tool_use blocks) to history
            conversation_history.append({
                "role": "assistant",
                "content": response.content
            })
            
            # Process all tool calls in this response
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"  [Calling tool: {block.name}({json.dumps(block.input)})]")
                    
                    try:
                        result = execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result
                        })
                    except Exception as e:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Error: {str(e)}",
                            "is_error": True
                        })
            
            # Send tool results back to Claude
            conversation_history.append({
                "role": "user",
                "content": tool_results
            })
            
            # Loop continues — Claude will either call more tools or give final answer
            
        elif response.stop_reason == "end_turn":
            # Claude is done — extract the text response
            assistant_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    assistant_text += block.text
            
            # Add final response to history
            conversation_history.append({
                "role": "assistant",
                "content": response.content
            })
            
            return assistant_text
        
        else:
            # Unexpected stop reason
            return f"[Unexpected stop reason: {response.stop_reason}]"
```

### Option B: SDK Tool Runner (Less Code, Less Control)

The Anthropic Python SDK includes a beta `tool_runner` that automates the loop:

```python
from anthropic import Anthropic, beta_tool
import json

client = Anthropic()

@beta_tool
def get_inventory_levels(skus: list[str] = None, category: str = "all") -> str:
    """Get current on-hand inventory counts for one or more SKUs.
    
    Args:
        skus: List of SKU codes to check. If empty, returns all.
        category: Filter by category - cheese, meat, accompaniment, packaging, or all.
    
    Returns:
        JSON string of inventory levels.
    """
    result = your_app.get_inventory_levels(skus=skus, category=category)
    return json.dumps(result, default=str)

@beta_tool
def get_shortfall_report(wave_date: str = None) -> str:
    """Calculate inventory shortfalls for an upcoming ship wave.
    
    Args:
        wave_date: Ship wave date (YYYY-MM-DD). Defaults to next wave.
    
    Returns:
        JSON string of shortfall analysis.
    """
    result = your_app.get_shortfall_report(wave_date=wave_date)
    return json.dumps(result, default=str)

# ... define other tools the same way ...

# Run the tool loop automatically
runner = client.beta.messages.tool_runner(
    model="claude-sonnet-4-6",
    max_tokens=4096,
    system="You are the Elevate Foods operations assistant...",
    tools=[get_inventory_levels, get_shortfall_report],
    messages=[{"role": "user", "content": "What's short for next wave?"}]
)

for message in runner:
    # Each iteration is either a tool call or final response
    print(message)
```

### Recommendation
Start with **Option A** (manual loop). It gives you full visibility into what's happening, makes debugging easier, and lets you add logging, rate limiting, and custom error handling. Move to Option B once the system is stable and you want cleaner code.

---

## 5. Building the Chat Interface

### Option 1: Tkinter GUI (Matches Existing App Style)

Since Roboute-kun is already a tkinter app, adding a chat tab or panel is the most natural integration.

```python
import tkinter as tk
from tkinter import scrolledtext
import threading

class ChatPanel(tk.Frame):
    def __init__(self, parent, tools, chat_function):
        super().__init__(parent)
        self.tools = tools
        self.chat_function = chat_function
        self.conversation_history = []
        
        # Chat display
        self.chat_display = scrolledtext.ScrolledText(
            self, wrap=tk.WORD, state='disabled',
            font=("Consolas", 10), bg="#1e1e1e", fg="#d4d4d4"
        )
        self.chat_display.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Input area
        input_frame = tk.Frame(self)
        input_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.input_field = tk.Entry(
            input_frame, font=("Consolas", 10)
        )
        self.input_field.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.input_field.bind("<Return>", self.send_message)
        
        self.send_button = tk.Button(
            input_frame, text="Send", command=self.send_message
        )
        self.send_button.pack(side=tk.RIGHT, padx=(5, 0))
    
    def append_to_chat(self, sender, message):
        self.chat_display.config(state='normal')
        self.chat_display.insert(tk.END, f"\n{sender}: {message}\n")
        self.chat_display.config(state='disabled')
        self.chat_display.see(tk.END)
    
    def send_message(self, event=None):
        user_input = self.input_field.get().strip()
        if not user_input:
            return
        
        self.input_field.delete(0, tk.END)
        self.append_to_chat("You", user_input)
        
        # Run API call in background thread to keep GUI responsive
        def run_chat():
            self.send_button.config(state='disabled')
            self.append_to_chat("System", "Thinking...")
            
            try:
                response = self.chat_function(
                    user_input, 
                    self.conversation_history, 
                    self.tools
                )
                # Remove the "Thinking..." message would go here
                self.append_to_chat("Assistant", response)
            except Exception as e:
                self.append_to_chat("Error", str(e))
            finally:
                self.send_button.config(state='normal')
        
        thread = threading.Thread(target=run_chat, daemon=True)
        thread.start()
```

### Option 2: CLI / Terminal Interface (Simplest)

For quick prototyping:

```python
def main():
    conversation_history = []
    
    print("=== Elevate Foods Operations Assistant ===")
    print("Ask me about inventory, subscriptions, shipping, or shortfalls.")
    print("Type 'quit' to exit, 'clear' to reset conversation.\n")
    
    while True:
        user_input = input("You: ").strip()
        
        if user_input.lower() == 'quit':
            break
        elif user_input.lower() == 'clear':
            conversation_history = []
            print("[Conversation cleared]\n")
            continue
        elif not user_input:
            continue
        
        print("  [Thinking...]")
        response = chat(user_input, conversation_history, tools)
        print(f"\nAssistant: {response}\n")

if __name__ == "__main__":
    main()
```

---

## 6. System Prompt Engineering

The system prompt is critical — it determines how Claude interprets data and makes recommendations. Here's a production-quality starting point:

```python
SYSTEM_PROMPT = """You are the Director of Operations assistant for Elevate Foods / AppyHour Box, 
a premium cheese and charcuterie subscription box service that ships perishable products nationwide.

## Your Role
You help with day-to-day operational decisions by analyzing live data from inventory, 
subscriptions, orders, and shipping systems. You are direct, specific, and action-oriented.

## Business Context
- Subscription boxes ship in "waves" (typically monthly)
- Products are perishable — cold-chain logistics matter
- Primary fulfillment through RMFG (Texas hub) and Tennessee hub
- Carriers: FedEx, UPS, LaserShip
- Subscription platform: Recharge (integrates with Shopify)

## SKU Conventions
- CH- prefix: Cheese (e.g., CH-GOUDA-SLC)
- MT- prefix: Meat/charcuterie (e.g., MT-SOPRES-SLC)
- AC- prefix: Accompaniments (crackers, jam, etc.)
- PK- prefix: Packaging (boxes, liners, gel packs)
- Box suffixes denote flavor profiles: MONG, OWC, SPN, MDT, BYO, ALPN, SS, ISUN, HHIGH, MS, NMS
- CEX-EC: Curator's Choice add-on; CEX-EC-[SUFFIX] matches box flavor
- PR-CJAM-[SUFFIX]: Bonus cheese & jam pairing

## How to Respond
1. ALWAYS use tools to check real data before answering. Never guess.
2. When assessing readiness for a wave, check: inventory, incoming POs, subscription counts, 
   and calculate shortfalls.
3. Report specific numbers: "You have 340 units on hand, need 500, short by 160."
4. Check if incoming shipments cover shortfalls before recommending action.
5. Flag critical risks first, then secondary concerns.
6. Recommend specific actions with priority:
   - URGENT: Needs action today
   - SOON: Needs action this week  
   - MONITOR: Watch but no action needed yet
7. Keep responses concise. Use tables for multi-SKU comparisons if helpful.
8. If you're not sure, say so — don't fabricate data.

## Example Interaction
User: "What should I be worried about for next wave?"
→ Call get_shortfall_report, get_incoming_shipments, get_subscription_counts
→ Cross-reference the data
→ Report: what's short, what's covered by incoming, what needs immediate action
"""
```

---

## 7. Connecting to Your Existing Data Functions

The key integration point is the `execute_tool()` function from Section 4. Each tool name maps to an existing function in your app. Here's how to adapt your existing code:

### Pattern: Wrapping Existing Functions

```python
# Your app probably has functions like these already:
# your_app.query_inventory(sku_list)
# your_app.get_recharge_subscriptions(wave_date)
# your_app.check_incoming_pos(date_range)

def execute_tool(tool_name: str, tool_input: dict) -> str:
    """
    Adapter layer between Claude's tool calls and your existing functions.
    Handles parameter mapping and response formatting.
    """
    try:
        if tool_name == "get_inventory_levels":
            # Map Claude's parameters to your function's signature
            skus = tool_input.get("skus", None)
            category = tool_input.get("category", "all")
            
            # Call your existing function
            raw_data = your_app.query_inventory(sku_list=skus, category=category)
            
            # Format for Claude (keep it readable but complete)
            result = {
                "total_skus": len(raw_data),
                "items": [
                    {
                        "sku": item.sku,
                        "name": item.product_name,
                        "on_hand": item.quantity,
                        "category": item.category,
                        "last_updated": str(item.last_updated)
                    }
                    for item in raw_data
                ]
            }
            return json.dumps(result, default=str)
        
        elif tool_name == "get_shortfall_report":
            wave_date = tool_input.get("wave_date", None)
            
            # This might combine multiple internal queries
            inventory = your_app.query_inventory()
            subscriptions = your_app.get_recharge_subscriptions(wave_date)
            incoming = your_app.check_incoming_pos()
            
            # Calculate shortfalls
            shortfalls = []
            for sku, demand in subscriptions.items():
                on_hand = inventory.get(sku, 0)
                incoming_qty = incoming.get(sku, 0)
                net = on_hand + incoming_qty - demand
                if net < 0:
                    shortfalls.append({
                        "sku": sku,
                        "demand": demand,
                        "on_hand": on_hand,
                        "incoming": incoming_qty,
                        "shortfall": abs(net),
                        "incoming_covers": incoming_qty >= (demand - on_hand)
                    })
            
            return json.dumps({
                "wave_date": str(wave_date),
                "total_shortfalls": len(shortfalls),
                "shortfalls": sorted(shortfalls, key=lambda x: x["shortfall"], reverse=True)
            }, default=str)
        
        # ... other tools ...
        
    except Exception as e:
        return json.dumps({"error": str(e), "tool": tool_name})
```

### Tips for Data Formatting
- **Return JSON strings** — Claude reads structured data well
- **Include context** — "last_updated" dates, totals, counts help Claude reason
- **Keep payloads reasonable** — If inventory has 500 SKUs, filter or summarize before sending. Token costs and context window limits matter.
- **Use `default=str`** in json.dumps to handle datetime objects, Decimals, etc.

---

## 8. Advanced: Multi-Tool Reasoning

Claude can call multiple tools in a single turn, or chain tools across turns. This is where the system gets powerful.

### Example: Wave Readiness Check
When the user asks "Am I ready for next wave?", Claude might:

1. **Turn 1:** Call `get_subscription_counts` AND `get_shortfall_report` simultaneously
2. **Turn 2:** Based on shortfalls, call `get_incoming_shipments` for specific SKUs
3. **Turn 3:** Synthesize everything into a recommendation

The manual loop in Section 4 handles this automatically — it keeps looping until Claude returns `end_turn` instead of `tool_use`.

### Parallel Tool Calls
Claude can request multiple tools at once. The response will contain multiple `tool_use` blocks. Process all of them and return all results in a single message:

```python
# In the loop, this is already handled:
tool_results = []
for block in response.content:
    if block.type == "tool_use":
        result = execute_tool(block.name, block.input)
        tool_results.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": result
        })

# All results sent back at once
conversation_history.append({
    "role": "user",
    "content": tool_results
})
```

---

## 9. Conversation Memory and Context

### Within a Session
The `conversation_history` list maintains context within a session. Claude can reference earlier questions and answers.

### Token Management
Each message in the history consumes tokens. For long sessions:

```python
def trim_history(conversation_history, max_messages=20):
    """Keep conversation manageable by trimming old messages."""
    if len(conversation_history) > max_messages:
        # Keep the first message (usually important context) and recent messages
        conversation_history = conversation_history[:1] + conversation_history[-(max_messages-1):]
    return conversation_history
```

### Across Sessions (Optional Future Enhancement)
To maintain context across app restarts, save conversation history to a JSON file:

```python
import json
from pathlib import Path

HISTORY_FILE = Path("chat_history.json")

def save_history(history):
    # Note: content blocks need serialization
    serializable = []
    for msg in history:
        if isinstance(msg["content"], list):
            # Handle content blocks (tool results, etc.)
            serializable.append({
                "role": msg["role"],
                "content": [
                    block if isinstance(block, dict) else block.__dict__
                    for block in msg["content"]
                ]
            })
        else:
            serializable.append(msg)
    HISTORY_FILE.write_text(json.dumps(serializable, default=str, indent=2))

def load_history():
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text())
    return []
```

---

## 10. Error Handling and Edge Cases

```python
def chat_with_error_handling(user_message, conversation_history, tools):
    """Wrapper with retry logic and error handling."""
    import time
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return chat(user_message, conversation_history, tools)
        except anthropic.RateLimitError:
            wait_time = 2 ** attempt  # Exponential backoff
            print(f"  [Rate limited, waiting {wait_time}s...]")
            time.sleep(wait_time)
        except anthropic.APIConnectionError:
            print("  [Connection error — check internet]")
            return "I couldn't reach the API. Check your internet connection."
        except anthropic.AuthenticationError:
            return "API key is invalid. Check ANTHROPIC_API_KEY."
        except Exception as e:
            print(f"  [Unexpected error: {e}]")
            return f"Something went wrong: {str(e)}"
    
    return "Failed after multiple retries. Try again in a minute."
```

---

## 11. Quick Start Checklist

1. [ ] `pip install anthropic` in your Anaconda environment
2. [ ] Get API key from console.anthropic.com
3. [ ] Set ANTHROPIC_API_KEY environment variable
4. [ ] Identify 3-5 data functions in the existing app to expose as tools
5. [ ] Write tool definitions (name, description, input_schema) for each
6. [ ] Implement `execute_tool()` to route tool calls to your functions
7. [ ] Copy the `chat()` function from Section 4
8. [ ] Write the system prompt (Section 6)
9. [ ] Add a CLI interface for testing (Section 5, Option 2)
10. [ ] Test with real questions: "What's short for next wave?"
11. [ ] Once working, integrate into the tkinter GUI as a chat panel
12. [ ] Tune the system prompt based on how Claude handles your data

---

## 12. Cost Optimization Tips

- Use **claude-sonnet-4-6** (not Opus) for 90%+ of queries — it handles tool-use just as well
- Keep tool result payloads concise — summarize large datasets before returning
- Trim conversation history to ~20 messages to avoid ballooning token usage
- For repeated/scheduled queries (like daily morning briefing), cache results and only re-query if data changed
- The manual loop lets you add logging to track token usage per query

---

## Appendix A: Full Minimal Working Example

```python
"""
Minimal working example of conversational ops assistant.
Copy this file, replace the dummy functions with real data connections.
"""
import anthropic
import json

client = anthropic.Anthropic()

# ---- REPLACE THESE WITH YOUR REAL FUNCTIONS ----
def real_get_inventory(skus=None, category="all"):
    """Replace with actual inventory query."""
    return [
        {"sku": "CH-GOUDA-SLC", "name": "Gouda Sliced", "on_hand": 340, "category": "cheese"},
        {"sku": "MT-SOPRES-SLC", "name": "Sopressata Sliced", "on_hand": 120, "category": "meat"},
    ]

def real_get_shortfalls(wave_date=None):
    """Replace with actual shortfall calculation."""
    return [
        {"sku": "CH-GOUDA-SLC", "demand": 500, "on_hand": 340, "incoming": 200, "shortfall": 0},
        {"sku": "MT-SOPRES-SLC", "demand": 500, "on_hand": 120, "incoming": 100, "shortfall": 280},
    ]
# ---- END REPLACEMENTS ----

tools = [
    {
        "name": "get_inventory_levels",
        "description": "Get current inventory counts. Use when asked about stock levels.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skus": {"type": "array", "items": {"type": "string"}, "description": "SKUs to check"},
                "category": {"type": "string", "description": "cheese, meat, accompaniment, packaging, or all"}
            }
        }
    },
    {
        "name": "get_shortfall_report",
        "description": "Calculate shortfalls for upcoming wave. Use when asked what's short or about wave readiness.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wave_date": {"type": "string", "description": "Wave date YYYY-MM-DD"}
            }
        }
    }
]

def execute_tool(name, inputs):
    if name == "get_inventory_levels":
        return json.dumps(real_get_inventory(**inputs), default=str)
    elif name == "get_shortfall_report":
        return json.dumps(real_get_shortfalls(**inputs), default=str)
    return json.dumps({"error": f"Unknown tool: {name}"})

def chat(user_msg, history):
    history.append({"role": "user", "content": user_msg})
    
    while True:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system="You are the Elevate Foods operations assistant. Use tools to check real data before answering. Be specific with numbers and recommend actions.",
            tools=tools,
            messages=history
        )
        
        if resp.stop_reason == "tool_use":
            history.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if block.type == "tool_use":
                    print(f"  [Tool: {block.name}]")
                    r = execute_tool(block.name, block.input)
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": r})
            history.append({"role": "user", "content": results})
        else:
            text = "".join(b.text for b in resp.content if hasattr(b, "text"))
            history.append({"role": "assistant", "content": resp.content})
            return text

if __name__ == "__main__":
    history = []
    print("=== Elevate Foods Ops Assistant ===")
    print("Type 'quit' to exit\n")
    while True:
        msg = input("You: ").strip()
        if msg.lower() == "quit":
            break
        if msg:
            print("  [Thinking...]")
            print(f"\nAssistant: {chat(msg, history)}\n")
```

---

*Document prepared for use as a technical reference by the implementing Claude Code instance (brother). All code examples use the current Anthropic Python SDK conventions as of March 2026.*
