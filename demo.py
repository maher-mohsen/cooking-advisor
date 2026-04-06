import streamlit as st
import asyncio
import os
import nest_asyncio
import time
from dotenv import load_dotenv
from anthropic import Anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

nest_asyncio.apply()
load_dotenv()

# --- Page Configuration ---
st.set_page_config(page_title="Cooking Advisor", page_icon="🍳", layout="wide")
st.title("🍳 Cooking Advisor")
st.info("I am strictly limited to the recipe database. I cannot provide information outside of my retrieved data.")

# --- System Prompt (Strict Closed-Domain) ---
SYSTEM_PROMPT = """You are the Cooking Advisor. 
STRICT RULES:
1. You are PROHIBITED from providing any recipes, ingredients, directions, or pairings that are not explicitly found in the retrieved tool data.
2. If a user asks for something outside the provided data, say: "I'm sorry, I don't have information on that specific request in my database."
3. Do not use your general training data to 'fill in the blanks'.
4. Use the conversation history to refine searches."""

# --- Core MCP Logic ---
async def fetch_tools():
    server_params = StdioServerParameters(
        command="uv",
        args=["run", "MCP/server.py"],
        env=os.environ.copy(),
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            response = await session.list_tools()
            return [{
                "name": t.name,
                "description": t.description,
                "input_schema": t.inputSchema
            } for t in response.tools]

async def call_mcp_tool(tool_name, tool_args):
    server_params = StdioServerParameters(
        command="uv",
        args=["run", "MCP/server.py"],
        env=os.environ.copy(),
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments=tool_args)
            return result

# --- Session State ---
if "messages" not in st.session_state:
    st.session_state.messages = []
if "mcp_tools" not in st.session_state:
    st.session_state.mcp_tools = []

# Automatic Connection
if not st.session_state.mcp_tools:
    try:
        st.session_state.mcp_tools = asyncio.run(fetch_tools())
    except Exception as e:
        st.error(f"Failed to auto-connect to MCP: {e}")

# --- Render Chat History ---
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        content = msg["content"]
        if isinstance(content, str):
            st.markdown(content)
        elif isinstance(content, list):
            for block in content:
                block_type = getattr(block, 'type', None) or (block.get('type') if isinstance(block, dict) else None)
                if block_type == 'text':
                    text = getattr(block, 'text', None) or block.get('text')
                    st.markdown(text)
                elif block_type == 'tool_result':
                    res_content = getattr(block, 'content', None) or block.get('content')
                    with st.expander("🛠️ Tool Result Data", expanded=False):
                        st.json(res_content)

# --- Chat Input ---
if prompt := st.chat_input("Search for pasta with chicken..."):
    st.chat_message("user").markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})
    
    client = Anthropic()
    
    with st.chat_message("assistant"):
        status_placeholder = st.empty()
        
        # Initial Thinking State
        status_placeholder.markdown("Cooking Advisor is thinking . . .")
        
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=st.session_state.mcp_tools,
            messages=st.session_state.messages
        )
        
        while response.stop_reason == "tool_use":
            st.session_state.messages.append({"role": "assistant", "content": response.content})
            
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    # VISIBLE TOOL UI SECTION
                    with st.status(f"🛠️ Executing Tool: {block.name}", expanded=True) as status:
                        st.write("**Reasoning:** Searching the database for relevant recipes.")
                        st.code(f"Arguments: {block.input}")
                        
                        try:
                            result = asyncio.run(call_mcp_tool(block.name, block.input))
                            st.write("**Data Retrieved:**")
                            st.json(result.content)
                            
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result.content
                            })
                            status.update(label=f"✅ {block.name} successfully consulted", state="complete")
                        except Exception as e:
                            st.error(f"Search Error: {e}")
                            status.update(label="❌ Search failed", state="error")

            st.session_state.messages.append({"role": "user", "content": tool_results})
            status_placeholder.markdown("Formulating response . . .")
            
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                tools=st.session_state.mcp_tools,
                messages=st.session_state.messages
            )

        status_placeholder.empty()
        final_text = response.content[0].text
        
        # Typing Animation
        resp_area = st.empty()
        full_resp = ""
        for word in final_text.split(" "):
            full_resp += word + " "
            resp_area.markdown(full_resp + "▌")
            time.sleep(0.04)
        resp_area.markdown(full_resp)
        
        st.session_state.messages.append({"role": "assistant", "content": final_text})