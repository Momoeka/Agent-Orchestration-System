# Librarian, explained in plain words

The other docs are written for engineers. This one is written for humans. Read this first if
the codebase feels like too much.

## The problem

AI chat models answer **from memory**. They have never read *your* documents — your company
wiki, your product manual, your policies. Ask about those and they either shrug or, worse,
**make something up that sounds right**. Confident and wrong is the default failure mode.

## What RAG is

RAG (Retrieval-Augmented Generation) is a simple idea wearing a fancy name:

> **Don't answer from memory. Look it up first, then answer.**

When a question comes in, the system first **searches your documents**, pulls the five most
relevant passages, hands them to the AI and says: *"Answer using ONLY these passages. Mark
every sentence with the passage it came from. If the answer isn't in them, say so."*

That's the whole idea. Everything in this repo is just doing that idea properly.

## Why it's called Librarian

Picture a really good librarian. You ask a question; they don't answer off the top of their
head. They walk to the shelves, pull the right pages, photocopy them, and write you a short
answer **with page references** so you can check every claim yourself.

(It's also the second half of a pair: **Foreman**, Project 15, is an office of AI workers.
Its research worker will use Librarian as the office's library desk — the `search_docs` tool.)

## The pieces, in the same picture

**Cutting books into pages (chunking).** The AI can't be handed a whole book at once, so
every document is cut into pieces about a page long, called *chunks*. There are three ways to
cut, and all three are built: every 1,200 letters (dumb but reliable), at chapter headings
(respects how the author organised things), or where the *topic* changes (smartest, most
expensive). Later, a competition on the same exam decides which one wins — with numbers, not
opinions.

**Two ways to find the right page (hybrid search).** This is the heart of the project:

- **Search by meaning** — finds pages about the same *idea* even in different words.
  ("How do I get my money back" finds the refunds page.)
- **Search by exact words** — basically Ctrl+F. Primitive-sounding, but for technical docs
  it's gold: search for the code word `status_code` and you want the page that *literally
  contains* `status_code`.

Each one misses what the other catches, so **both run and the two lists are merged**. This
already worked in real life on the first day: the correct page was ranked 5th by
meaning-search and 3rd by word-search — merged, it came out **2nd**. Neither alone would have
put it where you'd see it. (`docs/diagrams/02-hybrid-retrieval.png` is that exact moment,
drawn.)

**The answer writer.** Takes the found pages, writes the answer, marks every sentence with
[1], [2] — which page it came from.

**The fact-checker.** A *second, different* AI re-reads every citation: "does page [1]
really say that?" Unsupported claims get flagged, never silently kept. This is the layer
almost nobody builds, and it's what makes this system trustworthy.

**Honest "I don't know."** If the answer genuinely isn't in the documents, the system says
exactly that — what it found, what it didn't, where to look instead. A refusal is a designed
answer here, not a failure.

**The web page.** Type a question, see the answer, the pages it used, and a confidence score.
A toggle shows hybrid vs meaning-only search side by side, so the project's main claim is
something you can *watch*, not something you're told.

## Try it yourself (5 minutes)

From the `librarian` folder in a terminal:

```
uv run pytest -q                          # the test suite — should all pass
uv run python scripts/fetch_corpus.py     # download the demo corpus (the FastAPI docs)
uv run python scripts/seed.py             # cut into chunks and index (needs Ollama running)
uv run python scripts/search.py "how do I return a custom JSONResponse with a status_code" --compare
```

The last command prints two lists — merged search vs meaning-only — and every result shows
its rank in each list, so you can see the merge working.

## Why this matters for the job hunt

RAG is the single most requested skill in AI engineering job posts. Most candidates show a
weekend toy: one PDF, meaning-search only, no citations, no checking. This one searches two
ways, proves which is better with numbers, cites its sources, fact-checks its own citations,
and refuses honestly. That's the difference between "I followed a tutorial" and "I engineered
a system."
