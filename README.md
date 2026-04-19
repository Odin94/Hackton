# Hackton - TumTum Study Coach

The TumTum Study Coach learns about your study material, your personality and goals, your schedule and deadlines and delivers tailored recommendations to optimize your student life right when you need them - from auto-generated quizzes to foster active recall to recreational events so you can recharge and continue performing optimally.

## Rough architecture
* Backend with integration into chatgpt / general llm chat
    * Runs on a scheduler to update recommendations, remind user of deadlines, reach out to ask questions etc as autonomous agent
    * Uses cognee to continuously learn about each user, can store study material, the users schedule, their goals and performance to deliver tailored recommendations at just the right time (eg. active recall quiz generated from your slides right after a lecture finishes)
    * Can schedule notifications that are sent to user as chat message as soon as they log in
    * Crawls web pages and uses LLM agent to find recreational student events to recommend, tailored to users preferences and goals

* Frontend (`frontend-demo`is the real one, `frontend` was used for testing)
    * Receive proactive chat notifications from your agent with recommendations, questions for feedback, study performance updates, deadline reminders etc.
    * Chat with AI agent for general questions
    * (testing `frontend` also includes Elevenlabs integration for voice chatting with the agent, but we didnt have time to put that in the real frontend in `frontend-demo` :c)


* Data
    * SQLite for hard data (user accounts, schedule, web crawl cache, chat history etc.)
    * cognee for soft data (study notes, user preferences, goals, desires etc.)


## How to run
Either run with [mprocs](https://github.com/pvolok/mprocs), [oprocs](https://github.com/Odin94/oprocs) or manually with the following commands

### Backend
```bash
cd backend
uv sync  # install dependencies
uv run uvicorn main:app --reload
```


### Frontend
```bash
* cd frontend-demo
* npm i  # install dependencies
* npm run dev
```
