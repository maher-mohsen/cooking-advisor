# Cooking Advisor - Technical Design Record

## 1. Data Decisions
**Subset Selection:**
We selected a subset of the dataset—the first 3,000 recipes (`recipes_first_3k.csv`)—to enable fast ingestion, prototyping, and indexing while providing a sufficiently diverse corpus to demonstrate the system's search and summarization capabilities. 

**Embedding & Chunking Strategy:**
Embedding raw, concatenated rows typically dilutes the semantic signal. Instead, we adopted a targeted embedding strategy: we solely embed the recipe **`title`** using the `bge-m3` model (1024 dimensions). Because recipe titles act as dense summaries of the core concept of the dish, they offer strong and highly accurate similarity matching. 

**Preservation for the Generation Layer:**
We deliberately preserved the structured attributes—`ingredients` (as arrays), `directions` (as arrays), and `NER` entities (Named Entity Recognition components extracted from ingredients)—in their raw format within MongoDB. This allows the generation layer (LLM) to perform precise filtering, format generation, and analytical assessments (e.g., verifying exact calorie limits or specific dietary requirements) on cleanly structured data without hallucinating details from poorly chunked strings.

## 2. Agent Design
**Routing and Tool Boundaries:**
The system uses a tool-calling agent configuration (Anthropic's Claude) integrated with the Model Context Protocol (FastMCP). Instead of hardcoding conditional routing layers (like regex classification or independent smaller LLM routers), we defined discrete, bounded tools that the primary LLM can invoke dynamically. 

The boundaries are defined as follows:
*   `mongo_query`: Handles exact match filtering, aggregations, counts, and metadata grouping. Restricted strictly to safe MongoDB operations to prevent injection.
*   `mongo_fuzzy_search`: Utilizes MongoDB Atlas Text Search. Specialized in typo-tolerance and string alignment across `title`, `NER`, and `ingredients`.
*   `mongo_vector_search`: Leverages MongoDB Atlas Vector Search against the embedded titles to locate conceptually similar dishes using semantic proximity.

**Why This Approach?** 
We chose dynamic LLM tool-calling over restrictive static routers because natural language queries often traverse rigid boundaries. A user query might require a semantic search followed immediately by an aggregated count limit. The LLM can interpret nuanced user constraints and organically route queries to the right tools, combining approaches when appropriate.

**Example Flows:**
*   **User:** *"How many recipes use salmon?"*
    *   **Routes to:** Structured Search (`mongo_query` / `$count` filtering `NER` for salmon) → Returns aggregate count.
*   **User:** *"Give me a healthy salmon recipe under 400 calories"*
    *   **Routes to:** RAG + structured filter (`mongo_vector_search` or `mongo_fuzzy_search` filtering by traits, optionally refined by the LLM reasoning step) → Returns summarized recipe details.

## 3. Search System
**Structured / Filtered Search:**
Enabled via the `mongo_query` tool, this system allows the LLM to write specific MongoDB filter and aggregation payloads. By safely executing `$match`, `$group`, and `$project` aggregations against the structured fields, the agent seamlessly answers macro-level questions:
*   *"How many recipes are from each source?"* -> `$group` by source, `$count`.
*   *"What are the most common ingredients?"* -> `$unwind` the `NER` array, `$group`, and `$sort`.
*   *"How many recipes are tagged as vegetarian?"* -> Filter operation verifying structured tags.

**Fuzzy Search:**
Handled via `mongo_fuzzy_search` leveraging a MongoDB Atlas Search index. Using a compound index across `title`, `NER`, and `ingredients` with a `maxEdits` configuration of 2 and `prefixLength` of 1, the system corrects user input errors effortlessly. 
*   **User:** *"chiken pasta with galic"*
    *   **Matches:** The fuzzy algorithm natively forgives the typos and aligns the search against the exact target: *"Chicken Pasta with Garlic"*.

## 4. RAG Pipeline
**Chunking & Retrieval Process:**
As established, embedding entire raw recipe texts is inefficient. Our strategy hinges on using the **`title`** as our primary semantic chunk. We embed titles using the `bge-m3` model during the ingestion pipeline.

When a query is triggered via `mongo_vector_search`, the user query is embedded on-the-fly, and MongoDB Atlas Vector Search calculates the cosine similarity against the `title` embeddings in the database. The returned documents exclude the bulky vectors but include the full raw payloads (`ingredients`, `directions`, `NER`). 

This allows the pipeline to effectively handle queries like:
*   *"What can I cook with chicken and rice?"* $\rightarrow$ Semantic retrieval captures related dishes, LLM verifies ingredients overlap.
*   *"Summarize how to make a chocolate cake"* $\rightarrow$ Vector retrieves highest scoring chocolate cake, LLM synthesizes the `directions` array.
*   *"Give me recipes similar to lasagna"* $\rightarrow$ Vector proximity easily establishes that "Lasagna" is highly similar to "Baked Ziti" or "Stuffed Shells", presenting related dishes without requiring exact keyword overlaps.

## 5. Multi-Turn Memory
Multi-turn context is seamlessly maintained utilizing the conversational state memory array (`st.session_state.messages`). Every interaction—including the exact JSON responses retrieved by earlier tool executions—is persisted in the active context window sent to the LLM.

*   **Turn 1:** *"Give me chicken recipes"* 
    *   **Action:** LLM calls `mongo_fuzzy_search`, retrieves a JSON list of 10 recipes, and renders the result. This JSON payload is appended to the session state.
*   **Turn 2:** *"Now only show quick ones"* 
    *   **Action:** The LLM reads the previous turns' JSON history. Recognizing the context is intact, it evaluates the `directions` lengths or instruction content of the *already retrieved* Turn 1 documents, filtering locally without requiring an unnecessary secondary database hit.
*   **Turn 3:** *"Summarize the first one"* 
    *   **Action:** The LLM references the top isolated result from Turn 2's evaluation from its existing history, formatting the final instructions seamlessly.
