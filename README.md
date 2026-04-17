# Hackton

## Milestones
* Memory layer with 1 semester of study data (Cognee)  -  Alex
* Backend that harnesses an LLM and connects to Cognee and a UI  -  Odin / Amin
* UI with Chatbot that connects to backend  -  Amin
* Backend that uses LLM to proactively ping user about quizzes, journaling, doing things
* (mini extension) progress / study success indicator - are you on track to pass your exam by deadline?


## How to run
Either run with [mprocs](https://github.com/pvolok/mprocs), [oprocs](https://github.com/Odin94/oprocs) or manually with the following commands

### Backend
```bash
uv sync  # install dependencies
uv run uvicorn main:app --reload
```


### Frontend
```bash
* npm i  # install dependencies
* npm run dev
```
