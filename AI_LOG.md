# AI log (Claude Code)

Tool: Claude Code (VS Code extension), one conversation thread, kept in full for the review.
> **Developer TODO:** replace the drafts below with your own words. Keep only prompts you actually sent, and
> describe a rejection you actually made. Do not invent any.

## Five most useful prompts

1. *(sent)* "read the claude.md and then there are two file in the docs\assingment … this is our assingment …
   there is also refernce material … read all this and finish the assingment for me please"
   Why useful: together with `CLAUDE.md` (Python everywhere, small verified stages, never fabricate results), it made
   Claude read both documents fully, inspect the real sitemap (1,540 articles) and ask four decisions before coding
   (API budget, embeddings, UI framework, how to handle meeting recordings).
2. *(sent)* Answers to the planning questions: "Local fastembed", "Streamlit", "I write scripts, you record".
   Why useful: it fixed the stack early, so no work was thrown away.
3. TODO (yours)
4. TODO (yours)
5. TODO (yours)

## One time I rejected the AI's answer

TODO: describe a real moment where you disagreed with Claude's output and why.

For honesty, these are corrections made during the session that came from **tests and measurements**, not from the developer:
the learned prompt that activated itself without passing the regression gate, the 0.45 abstention threshold that never fired,
and a test fake that parsed ids wrongly. Details are in EVALS.md, "Failures we found and fixed".
