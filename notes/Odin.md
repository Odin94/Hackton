# Intro - 19:00 or so

## HappyRobot
AI Agent platform, logistics, very horizontal - can build pretty much anything on it
Data/Insight/action combined (kinda sounds like ZeitAI tbh)
Workflow builder (connect your stuff in and let AI do things on it)

### Challenge
* Build an autonomous AI Sales Product
"don't just answer questions, do proactive sales things & remember context when customer switches from phone to chat"

## osapiens terra (Unicorn)
Detect deforestation using satellites - makes money by certifying farms as non-deforesting
Work with big companies that are difficult to talk to because they have many employees that all have different requirements

We can take a picture of a place every ~5 days, sometimes you have clouds noising up your data
There's free satellite data everyone can access, we should use that to help them detect deforestation

### Challenge
* Detect deforestation after 2020 from satellite data
* Develop a ML system that is robust to noisy imagery and generalizes globally (different regions can look very different)
* Requires some computer vision skills
* Prices: 4x 3d printers (also btw they're hiring)


## Spherecast
AI Supply Chain product from CPG (consumer packaged goods) industry
Supply chains are complex because warehouses and manufacturers and raw materials and delays, orders, transfers -> stocks running out, overstocking, waste

Spherecast hovers up your gmail, snowflake, excel data and fixes your supply chain woes (by emailing people)
(did this one AI video guy have two phones?)
(Is agnes a real physical product..?)

### Challenge
* Move into the real world. Currently there are suppliers, manufacturers and brands and Spherecast operates at single-node-level. But we want to go to a full network - run a full vertical (Supplier/Manufacturer/Brand in one). It's all a connected graph with subgraphs for brands; a lot of companies share similar manufacturers and suppliers (eg. supplements are basically all just whitelabel ingredients with brand packaging)
* Design and build an AI-powered decision support system - eg. we get the brand the lowest priced Vitamin C


## REEEEply
*naruto runs*
They do consulting for all sorts of tech things

### Challenge
* Hackathon folks are today's customer. You have a ton of systems (ZHS for sports, library for booking study space) and you're the human API connecting all these things.
* Think about what's bugging you in your day2day and use innovation and gen-ai, provide for yourself and your fellow students a campus co-pilot suite - agents for students (auto-books your study space for your breaks or something)
* Agent should be fully autonomous, not a chat interface. 
* 1. Identify the problem (ideally somethihng that bothers you)
* 2. Design the agent that *proactively* solves your issues and tackles beurocracy
* 3. Show how your solution makes your life better
* Price: Backpack, bottles, probably what they give their newjoiners

# Tech Partners

## Google
* Johannes Frese, startup lead at google, wants you to connect with him on linkedin
* AI startups that google sees: 1. Multimodal AI (products that hear & think and suck in all data), 2. Agents (chatbots, autonomous), 3. RAG, 4. Customer chatbots
* Session tomorrow does some google vibe codey thing (with vertex..?)
* Google will give a finalist $25k GCP credits lol - apply to their startup accelerator program! (they say)

## AMD
(something about their dev cloud)

## Cognee
Also helps you build AI Agents, open source
* Free access to their cloud solution - connect data in (dump in files), builds a knowledge graph, talk to your data (even via API)
* Basically vector db as a service
* $500 for best cognee

## Dify
Agentic workflows, knowledge pipelines, market place of plugins (also open source I think?)

## 11Elevenlabs
* Price: 1/2 year of scale tier

### Reply Challenge Ideas
* Auto-scheduler for friend group hangouts - automates all the annoying followups and pushing people to share their availability and interest  (boys calls, TTRPG sessions)
* Kleinanzeigen-matcher - Define types of produts you're interested in at what price, agent then scrapes Kleinanzeigen and pings you with recommendations  (cognee db of stuff you have and like to compare to stuff?)
* Weekly scraping of uni websites for things you may need (deadlines, signups, cool events, interesting courses etc.)
* Trouble was doing the smart thing for lectures (prepare by reading slides, go to tutoriums, do homework, read the recommended book chapters) - do a Whoop-style diary that learns what really helps you and recommends / shows the improvement to motivate you
* Alex' solution: Journal app where you fill I did xyz, attended this lecture (liked it/didn't like it), learns what helps you - starts out with obvious general help (other successful students do xyz for coldstart), learns what really helps you personally ("you ate spicy food and your studying session after was bad - may don't do that")
  * Smack all your slides into Cognee to have specific material recommendations you can use (specific "study this thing" recommendation")
  * Once it knows your full curriculum and learns from your personal input it can give holistic recommendations
  * Give you stats on how your improvement works
  * "Mentor that sees what's going well or poorly in your life and helps you improve"
  * Grows with you (onboarding buddy, personal recommendations, maybe expand into career stuff later on? Help you find thesis topic)
  * Multiple agents - one gives context and proposal, others judge (critic/researcher/proposer - focused on exams/personal goals/studying etc.)
  * Runs 24/7 on some server, connects to web & phone
  * Interaction: Text it throughout the day, maybe it pings you a questionnaire daily, maybe it reads your calendar to know when to ask you about things?
  * Dump curriculum and lecture slides
    * Quizzes you with mini exams to track your performance (mixture of open/closed questions, shows progress indicators)
    * Pings you with helpful advice in the moment ("go to bed soon", "have you studied today? You should", "Wanna do a quiz?", "I see that your calendar is X, don't you want to do Y?")
    * After quizzes it gives you feedback "something seems off, should we diagnose?", "you did well and are on a great path, treat yourself!"
  * "Deadline Agent" - reminds you of deadlines, hounds you about your plans - "if you keep studying like this, you will fail the exam!"
  
  * Core feature: 
    * Study quizzes & performance improvement suggestions based on surveys
    * Web App with 24/7 agent that can ping you
    * It has your curriculum in it's memory layer
    * Creates study plan with you and actively enforces that you follow it (hounds you, confirms with quizzes)
      * Quiz after lecture?
  
  * Components:
    * File/data upload
    * Websocket chat
    * Study plan wizard
    * Schedule setter
    * Agent harness that messages you 
    * Run all this stuff locally

  * Golden path:
    * Sign up
    * Chatbot interface that talks you through onboarding experience (Uni? Goals? Materials? Deadlines?)
    * Proactively pings you in chat "study this!" then "do this quiz I made!"
    * Shows you study success indicator, warns you if you go off track
  
  * Extensions:
    * Give it your TUM online credentials and it perpetually pulls study material / deadline info
    * Whatsapp connection / Telegram bot?
    * Calendar integration?
    * Reminder to keep uploading study materials
    * Public Demo on AWS
    * Tell it how far you got in the lecture ("only until page 30")
    * Deprecating/Archiving old, outdated knowledge

  * Demo:
    * Have the agent proactively ping you in demo ("you finished lecture XYZ today, do this quiz!")
    * Maybe time your phone/laptop to loudly ping you right when Alex's words-demo is done
    * 3-4 Live prompts ("what did I struggle with?", "What are my next steps?")

  * Remote APIs:
    * Cognee for study materials? (or do we just slap them into s3?) -> generate Quizzes#

  * Deliverables:
    * Memory layer with 1 semester of study data (Cognee)  -  Alex
    * Backend that harnesses an LLM and connects to Cognee and a UI  -  Odin  (have a loop where the LLM is triggered to ask if it wants to do one of 10 things - our individual features it can do)
    * UI with Chatbot that connects to backend  -  Amin
    * Backend that uses LLM to proactively ping user about quizzes, journaling, doing things
    * (mini extension) progress / study success indicator - are you on track to pass your exam by deadline?

### Reply Q&A

* They seem very student-focused (talking about moodle and tumonline and missing cool reply workshops at TUM, Uni knowledge)
* Really focused on the "campus copilot" and connecting systems
* Criteria: 25& Innovation/Ambition, 25% UI/UX, 25% Quality, 25% Presentation
* They give you an AWS account with credentials
* You can bother the REPLY folks and make them help you with your implementation
* Winners get to go to REPLY office for food, snacks and meeting REPLY employees
* "All student life improving solutions are accepted", solving beurocracy is a fav for them though



### Brainstorming issues
* Keep up with deadlines (interviews, exams, sign-ups, homework deadlines)
*



### Odin's backend LLM part thoughts
* We want some sort of cron-y thing that triggers the LLM and asks it if it wants to do things based on it's info
* TODOdin: Look up tools for building harnesses and triggering LLM with time
* What tools does our LLM have?
  * Create quiz
  * Modify schedule
  * (evaluate performance; we can probably do that with simple statistics?)

* Run schedules with good ol' python ("30min after lecture" we literally just run on a scheduler)
  * If we still have time, make it fully agentic (for the flavor)
