# 3-Minute Solution Video Script (Tie-Breaker Priority #1)
**Project**: GridWise — Smart Campus Energy Optimization Challenge  
**Event**: BUP CSE Fest 2026 Hackathon (Online Preliminary)  
**Target Duration**: 2 minutes 45 seconds (Limit: 3:00)

---

### [0:00 - 0:35] Scene 1: Introduction & Problem Context
- **Visual**: Show title slide / GitHub repository and problem statement overview.
- **Voiceover**:
  > "Hello judges and organizers! Today we present **GridWise**, our solution for the BUP CSE Fest 2026 Hackathon Smart Campus Energy Optimization Challenge.
  > The goal is to ingest 24-hour campus demand, rooftop solar forecast, and grid tariffs, together with unstructured natural language operator notes, and return both machine-checkable directives and a cost-minimized, constraint-checked 24-hour energy dispatch schedule."

---

### [0:35 - 1:20] Scene 2: End-to-End Pipeline Architecture
- **Visual**: Show architecture diagram in `README.md` or code structure in VS Code.
- **Voiceover**:
  > "Rather than treating language models as black-box arithmetic engines, our solution decouples semantic understanding from mathematical optimization via a 3-stage pipeline:
  > 1. **LLM Interpretation**: We leverage a configurable Google Gemini Flash model (default `gemini-2.5-flash`) with structured output to interpret 1 to 3 operator notes. The LLM extracts the exact directive type, start-inclusive / end-exclusive whole-hour intervals (such as 1 PM to 3 PM mapping to hours 13 and 14), computes solar reduction factors (converting an 80% reduction to a 0.2 multiplier), calculates battery reserves, and filters out distractor notes as `no_op`.
  > 2. **Deterministic Guardrails**: Before sending anything to the optimizer, deterministic validators sanitize the directives: ensuring unique ascending hours in `[0..23]`, bounding values within physical battery capacity, and enforcing that `applies=false` is strictly restricted to `no_op`.
  > 3. **Mathematical Optimizer**: Finally, the validated directives feed into a SciPy HiGHS Mixed-Integer Linear Program (MILP) to compute a constrained 24-hour dispatch schedule."

---

### [1:20 - 2:05] Scene 3: Optimization & Energy Balance Correctness
- **Visual**: Show `app/optimizer.py` and linear programming equations.
- **Voiceover**:
  > "Our optimization engine formulates the exact physical constraints:
  > - **Energy Balance**: Grid import plus solar used plus battery discharge equals campus demand plus battery charge for every hour.
  > - **Solar Curtailment**: Solar consumption never exceeds effective available solar.
  > - **Battery State Dynamics**: Battery state transitions follow $E_{\text{after}} = E_{\text{before}} + \text{charge} - \text{discharge}$, strictly respecting minimum reserve and capacity bounds.
  > - **End-of-Day Neutrality**: Final state of charge equals the initial state of charge ($E_{23} = E_0$), preventing one-time battery depletion.
  > Because HiGHS solves the encoded MILP objective subject to the hard constraints, it provides the lowest-cost feasible plan found within the configured solver budget."

---

### [2:05 - 2:45] Scene 4: Verification & Live Demonstration
- **Visual**: Run the repository test command and show the endpoint verification path.
- **Voiceover**:
  > "Let's run the regression suite and the public-case replay pipeline.
  > As you can see:
  > - `GET /health` responds with `status: ok` immediately.
  > - Structurally invalid requests return a controlled 400 Bad Request.
  > - The current regression suite checks energy balance, battery bounds, directive constraints, end-of-day neutrality, API totals, and replay validity.
  > The system is containerized with Docker, binds to port 8000 on 0.0.0.0, and contains zero hardcoded secrets.
  > Thank you!"
