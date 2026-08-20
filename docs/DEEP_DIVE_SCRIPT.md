# BODHI MULE HUNTER AI — Complete Deep Dive Script

**Kya hai ye:** Poore project ka ek-ek hissa — technical aur non-technical dono —
documentary style mein, Hinglish mein. Koi time limit nahi. Har cheez ka **kya,
kyun, kaise, kab, aur nahi to kya**.

**Kaise use karein:** Ye ek script bhi hai aur ek study document bhi. Poora padh
lijiye to project ki complete understanding ho jayegi — kisi bhi sawaal ka
jawaab de payenge. Presentation ke liye chhota version
`docs/PRESENTATION_SCRIPT.md` mein hai.

> Har number is document mein `artifacts/metrics/evaluation.json`,
> `artifacts/metrics/boi_track.json`, ya seedhe code se aaya hai. Kuch bhi yaad
> se nahi likha gaya.

**Chapters:**

1. [The Crime — paisa gaya kahan](#chapter-1)
2. [The Broken Alarm — aaj ka system](#chapter-2)
3. [The Blueprint — architecture aur data model](#chapter-3)
4. [The World We Built — data generator](#chapter-4)
5. [The 71 Features — feature engineering](#chapter-5)
6. [The Graph — network banana](#chapter-6)
7. [Three Brains — teen models, poori training](#chapter-7)
8. [The Judge — fusion aur calibration](#chapter-8)
9. [The Verdict — saare results](#chapter-9)
10. [The Explanation — SHAP, GNNExplainer, STR](#chapter-10)
11. [The Red Button — kill-switch aur audit](#chapter-11)
12. [SHIELD — malicious APK triage](#chapter-12)
13. [Their Dataset — organisers ka data](#chapter-13)
14. [The Machine Room — API, dashboard, tests, deploy](#chapter-14)
15. [Honest Limits aur The Bigger Game](#chapter-15)

---

## Opening

Julai 2026. RBI ki ek report aati hai jisme mule accounts par framework ki baat
hoti hai. Usi hafte NPCI UPI ke naye fraud numbers release karta hai. Aur usi
mahine, kisi chhote sheher mein, ek retired teacher apne bank ki branch mein
khadi hoti hain, ek printout haath mein liye, aur poochh rahi hoti hain ki unke
aath lakh rupaye kahan gaye.

Sunne mein ye do alag khabrein lagti hain. Lekin inke beech ek connection hai
jo notice karne jaisa hai.

Bank ke paas **pehle se** ek transaction monitoring system tha. Wo system us
raat bhi chal raha tha. Usne us raat alerts bhi generate kiye. Lekin us
teacher ka paisa phir bhi chala gaya.

To sawaal ye nahi hai ki "bank ke paas system kyun nahi tha". Sawaal ye hai ki
**system tha, phir bhi kyun nahi bacha?**

Aur uska jawaab ek aisi jagah chhupa hai jahan zyadatar log dekhte hi nahi —
system ne **bahut zyada** alert nikale, itne zyada ki koi insaan unhe padh hi
nahi sakta tha.

Aaj hum is poore problem ko, aur uske hamare solution ko, **pandrah chapters**
mein decode karenge. Har chapter mein sirf "kya banaya" nahi — **"kyun waise
banaya, kaise banaya, aur agar waise na banate to kya hota"** bhi.

Chaliye shuru karte hain.

---

<a name="chapter-1"></a>
## Chapter 1 — The Crime: paisa gaya kahan

### Wo teen minute

Shaam saat baje call aata hai. "Madam, KYC expire ho gaya hai." Ek link. Ek APK
install. Ek OTP.

Aur agle **teen minute** mein aath lakh rupaye nikal jaate hain.

Ab ye samajhna zaroori hai ki wo aath lakh **ek jagah nahi gaye**. Attacker
bewakoof nahi hai. Agar aath lakh ek hi khaate mein jaate, to wo khaata turant
flag ho jaata — kyunki ek normal savings account mein achanak aath lakh aana
har rule engine ko dikhta hai.

Isliye paisa **toota**. Sattaais alag khaaton mein. Har ek mein tees hazaar,
pachchees hazaar, aththaais hazaar. Aise amounts jo kisi bhi threshold se
neeche hain.

Phir un sattaais khaaton se paisa aage gaya — doosre khaaton mein. Phir teesre.
Har hop par thoda commission kat gaya. Aur chauthe ya paanchve step par, kisi
ATM se ya kisi AePS micro-ATM se, wo **cash** ban gaya.

Aur cash ban jaane ke baad? Cash ka koi audit trail nahi hota. Ussi second wo
paisa permanently gaya.

### Mule account hota kya hai — aur kya nahi hota

Sabse badi galatfehmi ye hai ki log samajhte hain mule account "fake" hota hai.

**Bilkul nahi.**

Mule account ek **poori tarah asli** khaata hai. KYC genuine hai. Aadhaar asli
hai. PAN asli hai. Video KYC hui hai. Jis insaan ke naam par hai wo insaan asli
hai, zinda hai, aur kal aapke saamne baith sakta hai.

Farak sirf ek hai — us khaate ka **control** kisi aur ke paas hai.

**Kaise?** Teen tareeke:

**Ek — Rent.** Kisi student ko, kisi daily-wage worker ko paanch-das hazaar
rupaye mahina milte hain, sirf apna khaata "use karne dene" ke liye. ATM card
aur SIM handler ke paas chala jaata hai.

**Do — Dhokha.** "Work from home job hai. Aapke account mein paisa aayega, aap
withdraw karke humein de dena, aapko 5% commission milega." Ye insaan khud ko
mujrim nahi samajhta. Kai baar use pata bhi nahi hota.

**Teen — Malicious app.** Wo trojan APK jo victim ke phone par install hui —
uske andar beneficiary account numbers **hardcoded** hote hain. Ye sabse
interesting case hai, aur hum ispar Chapter 12 mein wapas aayenge.

### Ring ke andar teen role hote hain

Ek mule ring ek flat structure nahi hoti. Usme division of labour hoti hai —
aur hamare simulator mein ye teen roles literally implement hain:

| Role | Kaam | Kitne | Signature |
|---|---|---|---|
| **COLLECTOR** | Victim ka paisa direct receive karta hai | ring ka ~40% | Bahut saare ajnabiyon se fan-in |
| **RELAY** | Paisa aage pass karta hai, layering | ring ka ~35% | High pass-through, low retention |
| **CASHOUT** | ATM/AePS se cash nikalta hai | ring ka ~25% | High cash ratio, zero end balance |

**Kyun ye important hai?** Kyunki teenon role **alag dikhte hain**. Ek model jo
sirf "fan-in" dhoondhta hai, wo collectors pakad lega aur cashouts chhod dega.
Isliye hum recall **role-wise** report karte hain — aur hamare numbers ye hain:
RELAY 100%, COLLECTOR 98.3%, CASHOUT 92.1%.

Cashout sabse mushkil hai. Kyunki cash withdrawal karna **crime nahi hai** —
lakhon log roz karte hain.

### Aur ab asli technical problem

*(Ye is poore project ka core hai. Dhyan se.)*

Agar aap us mule account ki taraf **akele** dekhein — sirf us khaate ko, apne
aap mein — to usme **kuch bhi galat nahi dikhta**.

Paise aaye. Paise gaye. Balance zero. Har transaction individually legal hai.
Har transaction limit ke andar hai. Koi rule technically toota hi nahi.

Jo cheez galat hai wo **do jagah** chhupi hai:

**Ek — paise ki SHAPE.** Chalees credits nabbe minute mein. Aur raat ke teen
bajkar baarah minute par pura balance khaali. Individually har transaction
normal hai — lekin unka **pattern** normal nahi hai.

**Do — us khaate ki SANGAT.** Uske counterparties kaun hain. Wo kis device se
login karta hai. Us device se aur kaun-kaun login karta hai. Uske padosi khud
kitne suspicious hain.

**Shape aur sangat.** Poora project inhi do shabdon par khada hai. Har design
decision, har model, har feature — sab in do cheezon ko express karne ki koshish
hai.

### Aur wo chhattis ghante

Ek aur cheez jo samajhni zaroori hai — **timing**.

Victim ko turant pata nahi chalta. Kai baar agli subah, kai baar do din baad.
Phir wo 1930 par call karta hai. Phir NCRP par complaint register hoti hai.
Phir wo ticket bank tak pahunchta hai.

Average lag — **chhattis ghante ya usse zyada**.

Aur chhattis ghante mein paisa cash ban chuka hota hai.

**Iska matlab kya hai?** Iska matlab ye hai ki agar aapka system **complaint ka
intezaar** karta hai, to aap **structurally, hamesha, haar chuke hain**. Aap
kabhi jeet hi nahi sakte.

Isliye is project ka sabse important design goal ye tha: **complaint aane se
pehle pakadna.** Aur hum ye measure karte hain — Chapter 9 mein aap dekhenge ki
hamari 64% detections aise accounts hain jinka kisi bhi ticket mein naam nahi
tha.

---

<a name="chapter-2"></a>
## Chapter 2 — The Broken Alarm: aaj ka system kyun fail karta hai

### Humne guess nahi kiya — humne wo system banaya

Ye kehna aasan hai ki "purane systems kaam nahi karte". Lekin ye ek claim hai,
aur claim ko prove karna padta hai.

Isliye humne **wo system banaya**. `bodhi/baselines.py` — aath rules ka ek
engine, bilkul waise jaise banks aaj deploy karte hain.

Kuch rules dekh lijiye:

| Rule | Kya check karta hai |
|---|---|
| `R01_HIGH_VALUE_FAN_IN` | 24 ghante mein 5+ alag payers se ₹5,00,000 se upar credit |
| `R07_NEW_ACCOUNT_TURNOVER` | 90 din se naya khaata, turnover ₹2,00,000 se upar |
| `R02_STRUCTURING` | Bahut saare transactions ₹45,000–₹50,000 band mein |
| `R04_DORMANT_REACTIVATION` | 180 din dormant, phir achanak high velocity |
| `R06_RAPID_CASHOUT` | Credit ke kuch ghanton mein cash withdrawal |

Ab dhyan dijiye — **ye rules bewakoof nahi hain.** Ye samajhdaar AML
professionals ne banaye hain. Har rule ek asli typology ko target karta hai.

### Ab dekhiye kya hua

Humne ye engine chalaya **12,000 khaaton** aur **8,34,738 transactions** par.

Us population mein asli mule the — **151**. Yaani **1.26%**.

Rule engine ne alerts raise kiye —

**9,647.**

*(Ek second rukiye.)*

Nau hazaar chhe sau sattaalis. Baarah hazaar khaaton mein se. Yaani **har paanch
mein se chaar khaate** par shak.

Aur un 9,647 mein se sahi kitne the? **128.**

Precision — **1.33%**.

### Iska matlab insaani bhasha mein

Ek analyst subah office aata hai. Uske saamne queue mein sau case hain. Wo pehla
case kholta hai — innocent. Doosra — innocent. Teesra — innocent.

Sau case mein se **ninyanve** innocent log hote hain. **Ek** sahi nikalta hai.

Ab imagine kijiye ye roz ho raha hai. Hafte bhar. Mahine bhar.

Kya hoga? Analyst ka dimaag **seekh jayega** ki "ye queue jhooth bolti hai".
Wo cases jaldi-jaldi close karne lagega. Wo detail nahi dekhega.

Isko kehte hain **alert fatigue** — aur ye is poori industry ki sabse badi,
sabse under-discussed problem hai.

Aur sabse khatarnak baat? Jab wo **asli** wala case aayega, tab bhi analyst usi
speed se close kar dega.

### To rules kamzor hain? — Nahi. Problem kuch aur hai

Ye samajhna sabse important hai.

Problem ye nahi hai ki rules kamzor hain. Problem ye hai ki rules ke paas
**vocabulary hi nahi hai**.

Ek rule ye keh **hi nahi sakta**: *"is khaate ke counterparties khud suspicious
hain."*

Kyun? Kyunki rule ek khaate ko **akele** dekhta hai. `WHERE amount > 500000 AND
payers > 5`. Ye SQL hai. Isme network ka concept hi nahi hai.

Ek rule ye bhi nahi keh sakta: *"is device se athhaarah alag customers login
karte hain, aur unme se teen already flagged hain."*

### Aur jab bhasha na ho, to kya hota hai

Jab aapke paas signal express karne ki bhasha nahi hoti, to aap ek hi cheez kar
sakte hain — **sensitivity badha do**.

Threshold girao. Aur zyada alert nikalo. Umeed karo ki asli wala unme aa jaye.

Aur wahi 9,647 ka number ban jaata hai.

Ye engineering ki galti nahi hai. Ye **expressiveness ki limitation** hai. Aur
isko theek karne ka tareeka "behtar rules" nahi hai — **alag mathematics** hai.

### Ek aur baat — hum baseline ko cherry-pick nahi kar rahe

Koi keh sakta hai: "aapne jaanbujhkar kharaab baseline banaya taaki aapka model
achha lage."

Isliye humne har rule ka **individual performance** bhi measure kiya hai. Jaise
`R07_NEW_ACCOUNT_TURNOVER` — 77 baar fire hua, 23 sahi, precision **29.87%**.
Ye ek achha rule hai!

Problem individual rules nahi hain. Problem ye hai ki jab aap aath rules ko `OR`
se jodte hain, tab har rule ke false positives **jud jaate hain** aur poora
system 1.33% par aa jaata hai.

---

<a name="chapter-3"></a>
## Chapter 3 — The Blueprint: architecture aur data model

### Nau layers, nau agents

BODHI ek monolith nahi hai. Ye **nau layers** hain, aur har layer ka ek **named
agent** hai jiska ek specific kaam hai. Ye `bodhi/engine/agents.py` mein
formally declared hai — har agent ye batata hai ki wo kya **consume** karta hai
aur kya **produce** karta hai.

| # | Agent | Kaam | Input | Output |
|---|---|---|---|---|
| L1 | `TransactionMonitoringAgent` | Cross-channel events + govt tickets ko ek schema mein laata hai | raw feeds | normalised events |
| L2 | `FeatureEngineeringAgent` | 71 behavioural features, point-in-time | normalised events | feature matrix |
| L3 | `GraphBuilderAgent` | Account/device/IP knowledge graph, incremental | normalised events | financial graph |
| L4 | `FraudDetectionAgent` | XGBoost screening — sasti majority nipta deta hai | feature matrix | tabular score + SHAP |
| L5 | `GraphIntelligenceAgent` | GraphSAGE — rings aur multi-hop structure | graph + features | graph score + embeddings |
| L6 | `TemporalIntelligenceAgent` | TGN memory — smurfing, rapid routing, dormant burst | normalised events | temporal score |
| L7 | `RiskFusionAgent` | Calibrated stacking → ek 0–100 number | saare sub-scores | risk score |
| L8 | `ExplainabilityAgent` | TreeSHAP + GNNExplainer + narrative | SHAP + graph + score | evidence |
| L9 | `AlertGenerationAgent` | Cases, proportionate actions, reversible kill-switch | score + evidence | alerts + actions |

**Kyun layers mein toda?** Teen wajah:

1. **Testability.** Har layer alag test ho sakti hai. 121 tests isiliye possible
   hain.
2. **Deployment.** L3, L5, L6 batch mein chalte hain (schedule par). L4 aur L7
   inline chal sakte hain. Ye split hi 0.054 ms latency ko possible banata hai —
   Chapter 14 mein detail.
3. **Accountability.** Jab kuch galat ho, aap bata sakte hain **kaunsi layer**
   ne galti ki.

### Repo ka structure — kya kahan hai

```
bodhi/
├── config.py              # SAARI configuration ek jagah — thresholds,
│                          # hyperparameters, regulatory constants, team
├── schemas.py             # Pydantic data model — Transaction, Account, etc.
│
├── data/
│   ├── typologies.py      # 7 laundering patterns ka catalogue
│   └── generator.py       # Poora simulated bank (1000+ lines)
│
├── features/
│   └── engineering.py     # 71 features, vectorised
│
├── graph/
│   ├── builder.py         # CSR graph banana
│   └── rings.py           # Louvain community detection + flow tracing
│
├── models/
│   ├── xgb_model.py       # Layer 4
│   ├── graphsage.py       # Layer 5 — pure NumPy
│   ├── temporal.py        # Layer 6 — pure NumPy
│   └── fusion.py          # Layer 7 — constrained stacking
│
├── explain/
│   ├── gnn_explainer.py   # Edge mask optimisation
│   └── narrative.py       # Evidence ko English mein likhna
│
├── actions/
│   ├── casebook.py        # Alert lifecycle
│   └── killswitch.py      # Containment with constraints
│
├── compliance/
│   ├── audit.py           # Hash-chained append-only log
│   ├── pii.py             # Keyed-HMAC pseudonymisation
│   └── reports.py         # STR aur CTR generation
│
├── shield/
│   ├── apk_triage.py      # Binary AXML + DEX mining
│   └── ioc_bridge.py      # APK se nikle IOCs ko graph mein daalna
│
├── feeds/
│   ├── ncrp.py            # Government cyber-fraud tickets
│   └── regulatory.py      # RBI/NPCI/CERT-In directives
│
├── boi/                   # Organisers ke apne dataset ka track
│   ├── schema.py          # 3,924 column grammar parser
│   ├── dataset.py         # Robust loader
│   ├── features.py        # Cross-column engineering
│   ├── model.py           # 4 strategies, honest CV
│   └── synth.py           # Stand-in data unke exact schema par
│
├── engine/
│   ├── agents.py          # 9 agents ka formal declaration
│   └── pipeline.py        # MuleHunterEngine — sab kuch jodta hai
│
└── api/
    ├── main.py            # FastAPI — 22 routes
    └── state.py           # Process-wide runtime state
```

**Ek design principle jo har jagah dikhta hai:** `config.py` mein sab kuch ek
jagah hai. Koi magic number code mein nahi hai. Agar CTR threshold badalta hai,
to ek line badalti hai — bees jagah nahi.

### Data model — Pydantic schemas

Ye `bodhi/schemas.py` mein hai. Ye batata hai ki system **kis cheez ko kya
samajhta hai**.

**`Transaction`** — ek normalised financial event:

```python
txn_id: str
timestamp: datetime
src_account: str
dst_account: str
amount: float                    # > 0 enforced
channel: Channel                 # UPI/IMPS/NEFT/RTGS/AEPS/ATM/CARD/WALLET
txn_type: TxnType                # P2P/P2M/CASH_WITHDRAWAL/SALARY/...
device_id: str | None
ip_address: str | None
src_vpa: str | None              # UPI handle
dst_vpa: str | None
beneficiary_age_hours: float | None   # beneficiary kitna purana hai
is_fraud: bool | None            # sirf simulation mein
```

**`beneficiary_age_hours` par dhyan dijiye.** Ye ek chhota sa field hai lekin
bahut powerful. Kyunki fraud mein beneficiary **abhi-abhi** add hua hota hai.
Regulatory config mein `fresh_beneficiary_hours = 24` hai — 24 ghante se naye
beneficiary ko "fresh" maana jaata hai.

**`Account`** — customer aur khaate ki static info:

```python
account_id, customer_id, bank_code, branch_code
open_date: datetime
kyc_level: KycLevel              # FULL / MIN_KYC / SMALL_ACCOUNT
customer_age: int
income_band: LOW | MID | HIGH
state: str                       # MH, DL, UP, KA, ...
is_mule: bool | None             # ground truth, sirf simulation
mule_typology: str | None
```

**`DeviceLink`** — device se account ka rishta:

```python
device_id, account_id, first_seen, last_seen, os
```

Ye ek chhoti si table hai lekin **poore graph ki reedh** hai. Ek device se
kitne accounts jude hain — yahi device farm ka signature hai.

**`FraudAlert`** — government cyber-fraud ticket:

```python
ticket_id, reported_at
source: NCRP | I4C | HELPLINE_1930 | BANK_CFR | PARTNER_BANK
victim_account, beneficiary_account, beneficiary_vpa
amount, fraud_category, narrative
```

**Dhyan dijiye — ye victim ka statement hai, ground truth nahi.** Victim kehta
hai "mera paisa is account mein gaya". Ye ek claim hai. Isiliye hum ise ek
**feature** ki tarah treat karte hain, label ki tarah nahi.

**`IoC`** — Indicator of Compromise:

```python
ioc_type: UPI_VPA | ACCOUNT | DEVICE | IP | URL | SHA256 | PHONE
value, confidence (0-1), malware_family, observed_at
```

**`RegulatoryFeedItem`** — RBI/NPCI/CERT-In se aane wale directives.

**Aur output side par:**

- `LayerScores` — chaaron sub-scores
- `Evidence` — ek explanation ka tukda: code, label, detail, contribution, severity
- `RingSummary` — detected ring
- `ActionRecord` — kill-switch ne kya kiya
- `AlertStatus` — OPEN / CONFIRMED / FALSE_POSITIVE / ESCALATED

### Risk bands — output kaise consume hota hai

```python
RISK_BANDS = [
    (0,  39, "SAFE"),
    (40, 64, "MEDIUM"),
    (65, 84, "HIGH"),
    (85, 100, "CRITICAL"),
]

ALERT_THRESHOLD = 40        # yahan se human investigator ko alert
KILLSWITCH_THRESHOLD = 85   # yahan se automation act kar sakti hai
```

**Kyun 0–100 aur bands?** Kyunki ek investigator "0.847 probability" par kaam
nahi karta. Wo "HIGH" par kaam karta hai. Ye supervisory practice se match karta
hai.

**Kyun 85 par kill-switch?** Kyunki 85+ CRITICAL band hai, aur uske neeche
automation **kuch bhi freeze nahi kar sakti**. Ye ek hard constraint hai —
Chapter 11 mein detail.

### Regulatory constants — India-specific

Ye `RegulatoryConfig` mein hain, aur ye **domain knowledge** hain, magic numbers
nahi:

```python
ctr_threshold        = ₹10,00,000   # PMLA Cash Transaction Report
upi_p2p_cap          = ₹1,00,000    # retail ke liye uncommon uske upar
structuring_band     = (₹45,000, ₹50,000)   # classic smurfing zone
dormancy_days        = 180          # RBI ki dormancy definition
fresh_beneficiary_hours = 24
str_filing_days      = 7            # STR kitne din mein file karna hai
```

**Structuring band par dhyan dijiye.** ₹45,000–₹50,000. Kyun? Kyunki bahut saare
channel limits aur reporting thresholds ₹50,000 par hain. Smurfer isse **thoda
neeche** rehta hai — jaanbujhkar. Ye pattern detect karna ek feature hai.

---

<a name="chapter-4"></a>
## Chapter 4 — The World We Built: data generator

### Pehla sawaal — simulate kyun kiya?

Ye sawaal har judge poochhega, aur iska jawaab clear hona chahiye.

Humein chahiye tha: account-level mule labels, **plus** device linkage, **plus**
IP linkage, **plus** transaction graph, **plus** government tickets — sab ek
saath, ek hi dataset mein.

Ye data:
- **Commercially sensitive** hai — koi bank apna fraud data export nahi karega
- **Personally identifying** hai — usme asli logon ke transactions hain
- **Legally restricted** hai — PMLA aur privacy laws ke under

Aur koi public dataset mein ye exist **nahi** karta. Kaggle par credit card
fraud datasets hain — lekin unme graph nahi hai, device nahi hai, tickets nahi
hain.

**To simulation ka alternative "real data" nahi tha. Alternative "koi
evaluation hi nahi" tha.**

### Doosra sawaal — to synthetic data par numbers ka kya matlab?

**Ye sabse important sawaal hai, aur main iska seedha jawaab dunga.**

Naive synthetic data par har model 0.99 AUC deta hai. Kyunki agar aap mule ko
"bahut zyada transactions wala account" bana denge, to XGBoost pehle epoch mein
hi seekh lega.

To humne kya kiya? Humne apne simulated bank mein **jaanbujhkar wo legitimate
accounts daale jo mule jaise hi dikhte hain.**

Inhe kehte hain **hard negatives**. Aur ye is poore project ka sabse imaandaar
hissa hai.

### Hard negatives — humne apne hi model ko todne ki koshish ki

**1. Business Correspondent agents (Bank Mitra)** — 302 accounts.

Ye kaun hain? Ye government scheme ke under kaam karne wale log hain jo gaon
mein banking service dete hain. Ek handheld device. Din bhar mein bees-tees
logon ka AePS transaction. Saara din cash dispensing.

Ab socho — **ek device, bahut saare accounts, continuous cash withdrawal.**

Ye **exactly** wahi fingerprint hai jo ek device farm ka hai.

**2. Community collectors** — 107 accounts.

Chit fund ke organiser. Tuition batch ka collector. Festival committee ka
treasurer. Ajnabiyon se burst mein paisa aata hai, aur **~90% kuch ghanton mein
aage chala jaata hai**.

Ye **exactly** collection mule ka pattern hai.

**3. Chhote businesses** — 168 accounts. Rozana bahut saare alag logon se heavy
P2P fan-in.

**4. Genuine dormant wake** — 168 accounts. Student ka khaata jo chhe mahine
soya tha, ab semester fees ke liye achanak active. Ye **exactly**
`DORMANT_BURST` typology jaisa lagta hai.

**5. Merchants** — 600 accounts. High volume, high fan-in.

**6. Ordinary retail** — 10,504 accounts. Aam log.

### Aur ab result

Ye chaar decoy populations humne **isliye** daale taaki hamara model unpar
phanse. Agar wo phans jaata, to hamara precision claim jhooth hota.

| Population | Accounts | False positive rate |
|---|---|---|
| BC agents (Bank Mitra) | 302 | **0.33%** |
| Community collectors | 107 | **2.80%** |
| Small businesses | 168 | **0.00%** |
| Genuine dormant wake | 168 | **3.57%** |
| Merchants | 600 | **0.00%** |
| Ordinary retail | 10,504 | **0.77%** |

**Yahi wo table hai jo hamare 99% precision claim ko meaning deta hai.**

Aur dhyan dijiye — dormant wake par 3.57% sabse zyada hai. Hum ise chhupa nahi
rahe. Ye batata hai ki genuine dormancy break aur rented account ke beech ka
farak sabse patla hai. Ye ek asli limitation hai.

### Simulator ke andar kya hai

`bodhi/data/generator.py` — 1,000+ lines. Poora ek bank.

**Default configuration:**

```python
n_accounts    = 12,000
n_days        = 120           # 3 March 2026 se 1 July 2026
mule_rate     = 0.012         # 1.2%
test_fraction = 0.25          # aakhri 25% out-of-time test ke liye
n_devices     = 9,000
n_merchants   = 600
seed          = 7             # reproducible
```

**Realistic behaviour kaise banaya:**

*Hour-of-day intensity.* Genuine retail payments raat ko kam hote hain, subah
aur shaam peak hoti hai. Isliye ek 24-value probability vector hai:

```python
_HOUR_WEIGHTS = [0.6, 0.35, 0.25, 0.2, 0.25, 0.6, 1.4, 2.6, 4.2, 5.6,
                 6.4, 6.6, 6.2, 5.4, 5.0, 5.2, 5.8, 6.6, 7.4, 7.8,
                 6.8, 4.6, 2.6, 1.3]
```

Raat 3 baje weight 0.2 hai, shaam 7 baje 7.8. **Ratio ~39x.**

*Income bands.* Teen bands, alag median amount aur alag daily rate:

| Band | Median amount | Daily txn rate | Population share |
|---|---|---|---|
| LOW | ₹620 | 0.16 | 46% |
| MID | ₹2,100 | 0.31 | 39% |
| HIGH | ₹7,800 | 0.62 | 15% |

*Amounts lognormal distribution se* — kyunki asli transaction amounts lognormal
hote hain, normal nahi.

*Channels amount ke hisaab se chunte hain* — ₹5 lakh RTGS se jaayega, ₹500 UPI
se. Ye `_channel_for()` function karta hai.

*Salary credits* mahine ki shuruat mein. *Cash withdrawals* alag pattern se.

### Saat typologies — literally implement

Ye `bodhi/data/typologies.py` mein declared hain, weights ke saath:

| Code | Naam | Weight | Ring size | Kya hota hai |
|---|---|---|---|---|
| `FAN_IN_AGGREGATION` | Fan-in aggregation | 22% | 3–8 | Dozens victims → ek collection account |
| `LAYERING_CHAIN` | Multi-hop layering | 20% | 4–10 | A→B→C→D minutes mein, har hop commission |
| `SMURFING` | Structuring | 16% | 5–14 | Bada amount sub-threshold tukdon mein |
| `DORMANT_BURST` | Dormant reactivation | 14% | 3–9 | 6 mahine dormant, phir high velocity |
| `DEVICE_FARM` | Account rental | 12% | 6–20 | Ek handler device, accounts ka stable |
| `RAPID_CASHOUT` | Rapid cash-out | 10% | 3–7 | Credit se cash minutes mein |
| `APK_HARVEST` | Malicious APK network | 6% | 4–12 | Trojan mein hardcoded beneficiaries |

**Kyun weights?** Kyunki asli duniya mein sab typologies barabar frequency mein
nahi hoti. Fan-in sabse common hai, APK harvest sabse kam.

**Kyun size ranges?** Kyunki ek layering chain 4–10 accounts ki hoti hai, aur ek
device farm 6–20 ki. Ye structure detection ko affect karta hai.

### Do smart cheezein generator mein

**1. "Quiet" rings — the hard positives.**

```python
quiet = rng.random() < 0.18
n_campaigns = 1 if quiet else rng.integers(1, 5)
```

18% rings sirf **ek chhota campaign** chalate hain, aur victim count ek-tihai
hota hai. Ye wo mule hain jo **loud nahi** hain. Agar aapka model sirf loud
mules pakadta hai, to ye aapko expose kar denge.

**2. Late rings — out-of-time test.**

```python
late_cut_day = int(cfg.n_days * (1 - cfg.test_fraction))   # din 90
is_late = rng.random() < cfg.test_fraction                  # 25% chance
```

Ek-chauthai rings **sirf aakhri window** mein active hote hain — training data
ke **baad**. Model ne unhe kabhi dekha hi nahi, kisi bhi roop mein.

**Ye asli generalisation test hai.** Aur hamara result: 3 late rings, 26 mules,
**26 detected, 100% recall**.

### Timing bhi realistic hai

Campaigns raat ya late evening mein zyada hote hain:

```python
c_ts = t0 + c_day * 86400 + rng.choice(
    [1, 2, 3, 4, 11, 14, 20, 21, 22, 23],
    p=[.10, .10, .09, .08, .08, .08, .09, .12, .14, .12]) * 3600
```

Raat 1–4 baje ka combined weight 37% hai — jabki genuine traffic mein wo <2% hai.

Aur delays typology ke hisaab se alag hain:

```python
delay = rng.uniform(180, 3*3600) if typ.code != "RAPID_CASHOUT" else ...
```

`RAPID_CASHOUT` mein cash withdrawal credit ke **300 second se 2 ghante** ke
andar hota hai. Baaki typologies mein 3 minute se 3 ghante.

### Tickets bhi realistic lag ke saath aate hain

Victim reports turant nahi aati. Generator `_build_feeds()` mein tickets ko
**reporting lag** ke saath emit karta hai — kai baar ghante, kai baar din.

Aur — ye important hai — **sabhi mules ke liye ticket nahi aata**. Hamare data
mein 151 mules the, lekin sirf **53** ka naam kisi ticket mein tha. **98 mules
aise the jinke baare mein kisi ne kabhi complaint hi nahi ki.**

Ye realistic hai. Aur ye hamare "independence from tickets" test ka base hai.

---

<a name="chapter-5"></a>
## Chapter 5 — The 71 Features: shape ko numbers mein badalna

### Kya banate hain

`bodhi/features/engineering.py` — har account ke liye **71 features**, saat
categories mein:

**1. Volume & value (11 features)**
`in_count`, `out_count`, `in_amount`, `out_amount`, `total_count`,
`log_in_amount`, `log_out_amount`, `avg_in_amount`, `avg_out_amount`,
`max_in_amount`, `median_in_amount`

*Log kyun?* Kyunki amounts lognormal hain. `log1p` lene se distribution
normal ke kareeb aa jaati hai aur trees behtar split karte hain.

**2. Flow shape (6 features)** — *ye sabse discriminative category hai*

| Feature | Kya batata hai |
|---|---|
| `passthrough_ratio` | out_amount / in_amount — 1.0 ke kareeb = pure relay |
| `retention_ratio` | 1 − passthrough — mule ke liye ~0 |
| `cashout_ratio` | cash_amount / in_amount |
| `rapid_passthrough_ratio` | 60 minute ke andar nikla paisa / in_amount |
| `median_in_to_out_min` | Paisa aane aur jaane ke beech median minute |
| `min_in_to_out_min` | Sabse tez pass-through |

**`rapid_passthrough_ratio` par dhyan dijiye.** Ye ek asli innovation hai. Ye
sirf "paisa nikla" nahi batata — ye batata hai ki **kitna paisa aane ke ek
ghante ke andar nikal gaya**. Ek normal account mein ye ~0 hoga. Ek relay mule
mein 0.9+.

**3. Counterparty structure (9 features)**
`unique_payers`, `unique_payees`, `fan_in_ratio`, `fan_out_ratio`,
`payer_concentration`, `payee_concentration`, `reciprocity`,
`unique_payer_per_day`, `in_out_degree_ratio`

**`reciprocity` sabse smart hai.** Ye poochhta hai: *kitne counterparties ke
saath paisa dono direction mein gaya?*

Normal life mein reciprocity high hoti hai — aap dost ko paise bhejte hain, wo
aapko bhejta hai. Mule mein reciprocity **~0** hoti hai — victim se paisa aata
hai, victim ko kabhi wapas nahi jaata.

**4. Timing (8 features)**
`active_days`, `txn_per_active_day`, `night_frac`, `weekend_frac`,
`hour_entropy`, `max_gap_days`, `silence_before_first_days`,
`dormancy_burst_score`, `recency_days`

**`hour_entropy`** — agar transactions din bhar failey hain to entropy high,
agar ek hi window mein concentrated hain to low. Mule campaigns concentrated
hote hain.

**`dormancy_burst_score`** — code mein comment hai: *"Long silence followed by
dense activity: the rented-account signature."* Ye do cheezon ko multiply karta
hai — kitni lambi khamoshi thi, aur uske baad kitni density aayi.

**5. Structuring / evasion**
₹45,000–₹50,000 band mein transaction count, round-amount fraction, threshold
proximity.

**6. Device & network hygiene**
`unique_devices`, `unique_ips`, `device_shared_accounts`,
`max_accounts_per_device`, IP diversity.

**`max_accounts_per_device` device farm ka direct detector hai.**

**7. Channel mix + static**
Har channel ka fraction, KYC level, account age, income band.

### Sabse important technical decision — point-in-time

Har feature `as_of` timestamp ke saath compute hota hai. Yaani agar hum din 90
ke liye features bana rahe hain, to **sirf din 90 tak ka data** use hota hai.

**Kyun ye critical hai?**

Agar aap galti se future ka data use kar lein — jise **leakage** kehte hain —
to aapka model:
- Lab mein **shandaar** lagega (0.99 AUC)
- Production mein **bekaar** nikal jayega

Aur sabse buri baat: aapko pata bhi nahi chalega, kyunki aapka test set bhi
usi leaked feature se bana hoga.

Isliye poora `build_account_features()` ek `as_of` parameter leta hai aur uske
baad ka koi bhi event dekhta hi nahi.

### Aur ek engineering trick jispar mujhe garv hai

Problem: 8,34,738 transactions, 12,000 accounts, aur har account ke liye rolling
window features chahiye — 1 ghanta, 24 ghante, 7 din.

Naive tareeka: har account ke liye Python loop. **Ye ghanton lega.**

Hamara tareeka — **composite key trick**:

```python
_KEY = 2_000_000_000

key = codes.astype(np.int64) * _KEY + ts.astype(np.int64)
hi  = codes.astype(np.int64) * _KEY + (ts + window_s).astype(np.int64)
i0  = np.searchsorted(key, key, side="left")
i1  = np.searchsorted(key, hi,  side="right")
counts = i1 - i0
```

**Ye kya kar raha hai?** Account code ko `2_000_000_000` se multiply karke
timestamp add kar diya. Ab ek single sorted int64 array mein har account ka
apna "block" hai, aur us block ke andar timestamps sorted hain.

Ab **ek global `searchsorted`** poore dataset ke liye per-account window count
de deta hai.

**Result:** poora feature engineering **~4 second** mein.

*Kyun `2_000_000_000`?* Kyunki Unix timestamps abhi ~1.7 billion hain. To
timestamp kabhi is multiplier se bada nahi hoga, aur codes overlap nahi karenge.
`int64` ka range 9.2 quintillion hai, to 12,000 accounts easily fit ho jaate
hain.

### Ek bug jo humne pakda — aur ye batata hai ki testing kyun zaroori hai

Intelligence features mein ek field tha `ncrp_recency_days`. Agar account kisi
ticket mein nahi tha, to hum usme **999** daal dete the (sentinel value).

Phir ek jagah code tha: `has_intelligence = intel.notna().any(axis=1)`.

**Problem:** 999 bhi `notna()` hai! To **har account** ke paas "intelligence" ho
gayi. Poora intel layer meaningless ho gaya.

Fix: ek explicit `INTEL_EVIDENCE_COLUMNS` list banayi, aur `has_intelligence()`
sirf unhi columns ko dekhta hai jinme actual evidence hoti hai.

**Sabak:** sentinel values khatarnak hote hain. Aur ye bug production mein
kabhi na pakda jaata — model bas thoda kharaab perform karta.

---

<a name="chapter-6"></a>
## Chapter 6 — The Graph: sangat ko structure mein badalna

### Graph mein kya hai

`bodhi/graph/builder.py`. Teen tarah ke **nodes**:

- **Accounts** — 12,000
- **Devices** — 9,000
- **IPs**

Aur edges:

| Edge | Meaning | Weight |
|---|---|---|
| Account → Account | Paisa gaya | Amount + count |
| Account ↔ Device | Is device se login | Frequency |
| Account ↔ IP | Is IP se activity | Frequency |

### CSR format kyun

Graph **Compressed Sparse Row** format mein store hota hai (`scipy.sparse`).

**Kyun?** Kyunki ek 12,000-node graph ka dense adjacency matrix 144 million
entries ka hoga — jabki actual edges shayad 2-3 lakh hain. **99.8% zero.**

CSR mein sirf non-zero entries store hoti hain, aur neighbour lookup O(1) mein
ho jaata hai. Isi wajah se GraphSAGE ka message passing sparse matrix
multiplication ban jaata hai — jo NumPy mein bahut fast hai.

### Ek important guard — `MAX_SHARED_FANOUT`

```python
MAX_SHARED_FANOUT = 25
```

**Problem kya thi?** Kuch devices se **bahut saare** accounts jude hote hain —
jaise public WiFi ka IP, ya ek bank branch ka shared terminal.

Agar hum bina limit ke edges banate, to ek IP node **hazaar** accounts ko jod
deta, aur GraphSAGE ka message passing meaningless ho jaata — sab kuch sabse
connected ho jaata.

Isliye code mein check hai `if k < 2 or k > MAX_SHARED_FANOUT: skip`. Yaani
dono taraf se filter:

- **k < 2** — sirf ek account wala device koi rishta hi nahi banata, useless hai
- **k > 25** — ek device se 25+ accounts matlab wo public infrastructure hai,
  signal nahi

Beech ka range hi asli linkage hai.

### Rings nikalna — Louvain community detection

`bodhi/graph/rings.py`. Ye algorithm graph mein aise clusters dhoondhta hai jo
andar se densely connected hain aur bahar se kam.

Lekin yahan humein **teen problems** aayi thi, aur unka solution batata hai ki
naive approach kyun fail karti hai.

**Problem 1: Fake rings.**

Shuruat mein hum sirf Louvain chala kar communities ko "rings" keh dete the.
Result? Ek BC-agent device cluster mein **ek** flagged account tha, aur hum
"51-account device farm detected" report kar rahe the.

**Ye sirf galat nahi — ye khatarnak hai.** Ye 50 innocent Bank Mitra agents ko
investigation mein daal deta.

**Solution — teen filters:**

```python
MIN_FLAGGED_SHARE = 0.35     # community ke 35% members flagged hone chahiye
MIN_RING_MEAN_RISK = 50.0    # community ka average risk 50+ hona chahiye
resolution = 4.0             # Louvain ki resolution
```

**`resolution = 4.0` kya karta hai?** Louvain ki default resolution 1.0 hoti
hai, jo badi communities banati hai. 4.0 par wo **chhoti, tighter** communities
banata hai. Hum tight clusters chahte hain, poore neighbourhoods nahi.

**Problem 2: Members vs associated.**

Ek ring mein kuch accounts confirmed suspicious hain, kuch bas connected hain.
Inhe ek saath report karna misleading hai.

Solution: `Ring` object mein do alag lists hain —
- `members` — flagged accounts
- `associated` — connected but not flagged

Investigator dono dekh sakta hai, lekin unhe alag samajh sakta hai.

**Problem 3: Flow tracing — time ko respect karna.**

Money trail dikhane ke liye humne `trace_flows()` banaya. Lekin ek naive path
finder ye path bana sakta hai:

```
A →(3 baje) B →(1 baje) C
```

**Ye impossible hai!** Paisa 3 baje A se B gaya, to wo 1 baje B se C nahi ja
sakta.

Solution: **time-respecting paths** — har agla hop ka timestamp pichhle se bada
hona chahiye. Ye ek chhota constraint hai lekin isse trails asli ban jaate hain.

---

<a name="chapter-7"></a>
## Chapter 7 — Three Brains: teen models, poori training

### Pehle — kyun teen?

Ek mule teen alag tareekon se ajeeb hota hai:

1. Uska **apna behaviour** ajeeb hai → tabular problem
2. Uski **sangat** ajeeb hai → graph problem
3. Uske events ka **order** ajeeb hai → sequence problem

Ek hi model se teenon nahi ho sakte. XGBoost graph nahi samajhta. GNN sequence
nahi samajhta.

**Aur sabse important design decision:**

> **Ye teen models ek doosre ka data nahi dekhte.**

XGBoost ko graph ka pata nahi. Graph model ko sequence ka pata nahi.

**Kyun deliberate?** Kyunki agar teenon ek hi cheez dekhenge, to teenon **ek hi
galti** karenge. Ensemble ka poora fayda tab hai jab models **alag evidence**
par khade hon — tabhi wo ek doosre ki galti pakad sakte hain.

---

### Layer 4 — XGBoost (`bodhi/models/xgb_model.py`)

**Kaam:** Cheap screening. Aasan majority ko sasta mein nipta dena.

**Hyperparameters** (`config.py` se):

```python
n_estimators          = 400
max_depth             = 6
learning_rate         = 0.06
subsample             = 0.85
colsample_bytree      = 0.8
min_child_weight      = 3.0
reg_lambda            = 2.0
max_scale_pos_weight  = 12.0
random_state          = 42
```

**Har ek kyun:**

`max_depth = 6` — bahut deep trees overfit karte hain, khaaskar jab positives
kam hon. 6 ek balanced choice hai.

`learning_rate = 0.06` + `n_estimators = 400` — chhoti learning rate zyada trees
ke saath. Ye slow but stable convergence deta hai.

`subsample = 0.85`, `colsample_bytree = 0.8` — har tree data ka 85% aur features
ka 80% dekhta hai. Ye **randomness** add karta hai jo overfitting kam karti hai.

`min_child_weight = 3.0` — ek leaf mein kam se kam itna weight hona chahiye. Ye
model ko ek-do outlier accounts par rule banane se rokta hai.

**`max_scale_pos_weight = 12.0` — ye sabse interesting hai.**

Class imbalance hai: 151 mules vs 11,849 normal. Ratio ~78:1. Normally aap
`scale_pos_weight = 78` set karenge.

Lekin humne ise **12 par cap** kiya hai. Kyun?

Kyunki high `scale_pos_weight` model ko **uncalibrated** bana deta hai. Wo har
cheez ko high probability dene lagta hai. Aur hamein calibration chahiye —
kyunki 85 par kill-switch chalta hai.

**Trade-off:** thoda recall kam, lekin probabilities meaningful. Hamare use case
mein ye sahi trade-off hai.

**Result:** ROC-AUC **0.9946**, PR-AUC **0.9540**.

---

### Layer 5 — GraphSAGE (`bodhi/models/graphsage.py`)

**Reference:** Hamilton, Ying, Leskovec — NeurIPS 2017.

**Architecture:** Two-layer mean-aggregator GraphSAGE with linear readout.

```python
hidden_dims  = (64, 32)      # do layers
fanouts      = (15, 10)      # pehli layer 15 neighbours, doosri 10
dropout      = 0.2
lr           = 0.01
weight_decay = 1e-5
epochs       = 60
batch_size   = 512
```

**Kaise kaam karta hai — simple bhasha mein:**

Har account ka ek feature vector hai. Ab:

**Round 1:** Har account apne padosiyon ke feature vectors ka **average** leta
hai, apne saath **concatenate** karta hai, aur ek learned matrix se multiply
karke 64-dimension mein map ho jaata hai.

**Round 2:** Wahi cheez dobara — lekin ab har padosi ke paas already uske
padosiyon ki information hai. To effectively har account **do hop** door tak
dekh raha hai.

**Formula:**

```
h_v^(k) = σ( W^(k) · [ h_v^(k-1) ‖ mean_{u ∈ N(v)} h_u^(k-1) ] )
```

**"Inductive" ka kya matlab?**

Ye sabse important property hai. Transductive GNNs (jaise vanilla GCN) sirf un
nodes par kaam karte hain jo training ke waqt maujood the. Naya node aaye to
poora model retrain karna padega.

GraphSAGE **aggregation function** seekhta hai, node embeddings nahi. To ek
bilkul naya account — jo aaj khula — uske padosiyon se turant embedding bana
sakta hai, **bina retrain kiye**.

**Bank ke liye ye essential hai.** Har roz hazaaron naye accounts khulte hain.
Aur mule accounts aksar **naye** hote hain.

**`fanouts = (15, 10)` kyun?** Kyunki agar aap saare neighbours lein, to ek
high-degree node ka computation blow up ho jayega. Sampling se compute bounded
rehta hai aur ek regularisation effect bhi milta hai.

**Result:** ROC-AUC **0.9962** — XGBoost se **thoda behtar**, akele.

Training mein 60 epochs, best validation AUC epoch 59 par **0.9953**.

---

### Layer 6 — Temporal Graph Network (`bodhi/models/temporal.py`)

**Reference:** Rossi et al. — ICML Workshop on Graph Representation Learning,
2020.

```python
max_events   = 64       # har account ke aakhri 64 events
hidden_dim   = 64       # memory vector ka size
time_dim     = 16       # time encoding ka size
lr           = 0.015
weight_decay = 1e-5
epochs       = 90
batch_size   = 384
```

**Kaise kaam karta hai:**

Har account ka ek **memory vector** hai — 64 dimensions. Shuruat mein zero.

Phir uske events ek-ek karke aate hain. Har event par:

1. Event ka **message** banta hai — amount, channel, direction, aur **time
   since last event**
2. Time ko ek **learnable time2vec encoding** se encode kiya jaata hai
3. Memory ek **GRU** se update hoti hai

```
m_v(t) = [ msg(event) ‖ φ(Δt) ]
s_v(t) = GRU( m_v(t), s_v(t⁻) )
```

**`φ(Δt)` — time2vec — kyun learnable?**

Aap seedhe seconds daal sakte the. Lekin phir model ko khud seekhna padega ki
"5 minute" aur "5 din" mein kya farak hai — aur wo scale ka farak hai, linear
nahi.

Learnable encoding model ko ye **choose** karne deta hai ki kaunse time scales
important hain. Ek scale "minutes" pakad sakta hai, doosra "days".

**Ye kya pakadta hai jo baaki nahi pakadte?**

**Order.** Chalees credits aana ek baat hai. Chalees credits aana **aur phir
turant** nikal jaana — bilkul doosri baat.

Ek tabular model dono ko same dikhega (same count, same amount). TGN ko farak
dikhega.

**Result:** ROC-AUC **0.9628**, PR-AUC **0.8036**. Best val AUC epoch 52 par.

---

### Aur ab wo hissa jispar mujhe sabse zyada garv hai

**PyTorch use nahi kiya. GraphSAGE aur TGN dono pure NumPy mein likhe hain,
gradients haath se derive karke.**

**Kyun?**

Do wajah — ek majboori, ek fayda.

**Majboori:** Development environment mein PyTorch download **blocked** tha
(proxy ne `download.pytorch.org` se 403 diya).

**Fayda — aur ye asli hai:** Ab poora engine ek **laptop CPU** par chalta hai.
Bina GPU. Bina CUDA. Bina kisi deep learning framework ke.

Requirements dekh lijiye — `numpy`, `pandas`, `scipy`, `scikit-learn`,
`xgboost`, `networkx`, `fastapi`. Bas.

**Ek bank ke liye ye deployment ka farak hai.** Ek bank ke security team ko
PyTorch (2 GB, hazaaron transitive dependencies) approve karana mahinon ka kaam
hai. Ye engine ek slim Python image mein chal jaata hai.

**Lekin — gradients sahi hain, ye kaise pata?**

Ye sabse important sawaal hai. Kyunki galat gradient se model train hoga, loss
girega bhi, lekin wo **galat** cheez seekh raha hoga.

Isliye `tests/test_gradients.py` hai. Ye **central finite differences** se
verify karta hai:

```
∂f/∂θ ≈ [ f(θ + ε) − f(θ − ε) ] / 2ε        (ε chhota)
```

Har parameter ke liye analytical gradient (jo humne derive kiya) aur numerical
gradient (jo finite difference se aaya) ko compare kiya jaata hai, **2e-5
tolerance** par.

**Ye teen tests hain, aur ye poore neural code ki guarantee hain.** Agar koi
gradient galat hota, ye test turant fail hota.

---

### Layer 4.5 — Intelligence

Ye ek model nahi hai, ye **evidence aggregation** hai.

Kya dekhta hai:
- NCRP/1930 tickets — ye account kisi complaint mein named hai?
- CERT-In IOCs — iska VPA/device/IP kisi malware family se juda hai?
- SHIELD se nikale hue accounts — kisi trojan mein hardcoded tha?
- RBI/NPCI directives

**Result:** ROC-AUC **0.6883**, PR-AUC **0.3774**.

**Ye sabse kamzor layer hai — aur hona hi chahiye.**

Kyun? Kyunki tickets **sirf 53 mules** ke baare mein the (151 mein se). Baaki 98
ke liye intel layer ke paas **kuch bhi nahi** tha.

**To phir ise rakha kyun?**

Kyunki jab intel available hoti hai, wo **bahut strong** hoti hai. Aur fusion
layer ise **0.155 weight** deti hai — matlab wo useful hai, lekin decisive nahi.

Ye hi to point hai — hum har layer ko utna hi weight dete hain jitna wo
**deserve** karta hai, measured.

---

<a name="chapter-8"></a>
## Chapter 8 — The Judge: fusion aur calibration

### Problem statement

Chaar numbers hain. Ek number chahiye. Kaise?

Naive answer: average le lo. **Ye galat hai**, aur main batata hoon kyun.

### Do properties jo negotiable nahi thi

**Property 1 — Monotonicity.**

> Agar **koi bhi** layer ka score badhta hai, to final risk score **kabhi ghat
> nahi sakta**.

**Kyun zaroori hai?** Socho agar aisa na ho. Ek investigator ko aap kaise
samjhayenge: *"Graph model ne zyada shak jataya, isliye final risk kam ho
gaya"?*

Wo system par kabhi bharosa nahi karega. Aur ek regulator ke saamne ye
defensible nahi hai.

**Aur ye asli problem hai, theoretical nahi.** Ek unconstrained stacker jo chhote
fold par fit hota hai, wo **khushi se** seekh leta hai ki "high temporal score
matlab safer" — kyunki us chhote fold mein coincidentally aisa pattern tha.

**Kaise enforce kiya:**

```python
from scipy.optimize import minimize

# weights non-negative constrained
result = minimize(loss, w0, method="L-BFGS-B",
                  bounds=[(0, None)] * n_features)
```

`L-BFGS-B` — bounded optimisation. Har weight ka lower bound 0 hai. Bas.

**Property 2 — Calibration.**

Score sirf ranking nahi de sakta.

Agar system kehta hai **"80% probability"**, to jitne accounts ko 80 kaha hai,
unme se **lagbhag 80%** actually mule hone chahiye.

**Kyun zaroori hai?**
- Bina calibration ke aap threshold set nahi kar sakte
- Risk-based capital allocation nahi kar sakte
- **Aur sabse important: 85 par automated kill-switch chalta hai.** Us number ka
  meaning hona chahiye.

**Kaise kiya:** Isotonic regression — ye ek monotone mapping seekhta hai raw
score se observed frequency par.

**Measured result:**
- Expected Calibration Error (ECE) — **0.0047**
- Brier score — **0.00125**

ECE 0.0047 matlab aadha percent se bhi kam ka gap. Ye bahut achhi calibration
hai.

### Aur ab technical detail — log-odds space kyun

Ye ek subtle lekin important choice hai.

Sub-scores probabilities hain — 0 se 1 ke beech. Aur ye **0 aur 1 par pile up**
karte hain.

Ab socho: 0.990 aur 0.9999 mein farak kitna hai?

Probability space mein — **0.0099**. Lagbhag kuch nahi.

Odds space mein — 0.990 ka odds 99:1 hai, 0.9999 ka **9999:1**. **Sau guna
farak!**

Aur alert ranking **exactly isi region** mein decide hoti hai — top 1% mein.

Isliye fusion log-odds space mein hoti hai:

```python
def _logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))
```

### Paanch meta-features, pandrah nahi

```python
STACK_FEATURES = ("xgb", "graph", "temporal", "intel", "max_logit")
```

Chaar layer scores, plus **`max_logit`**.

**`max_logit` kyun?** Kyunki *"ek layer bahut confident hai aur baaki chup hain"*
ek **alag situation** hai *"sab thoda elevated hain"* se.

Pehla case: ek layer ne kuch specific pakda. Doosra case: general noise.

Sirf weighted sum se ye farak express nahi hota. `max_logit` isse capture karta
hai.

**Aur sirf paanch kyun, pandrah nahi?** (Interactions, squares, ratios add kar
sakte the.)

Kyunki fusion fold **chhota** hai — aur usme positives kam hain. Har extra
meta-feature noise fit karne ka ek aur mauka hai.

### Chaar folds, teen nahi — ye sabse important methodological decision hai

Standard practice: train / validation / test.

Humne **chaar** folds banaye:

```python
train : 6,000   (50%)   — base models yahan fit hote hain
val   : 1,800   (15%)   — early stopping yahan hoti hai
fuse  : 1,800   (15%)   — stacker SIRF yahan fit hota hai
test  : 2,400   (20%)   — ek baar, ekdum end mein
```

**Chautha fold kyun?**

*(Ye dhyan se samjhiye — ye sabse subtle point hai poore project mein.)*

Agar aap stacker ko **validation fold** par fit karein, to ek problem hai:
validation fold ne base models ko **early-stop** kiya hai.

Matlab base models ne us fold par apna best performance dikhaya — wo unka
"lucky" fold hai. Aur us fold par unke scores **optimistically biased** hain.

Ab agar stacker unhi biased scores par weights seekhega, to wo un weights ko
**over-trust** karega.

Isliye ek dedicated `fuse` fold — jise **kisi bhi base model ne nahi dekha**.

**Ye ek line ka fix hai jo do din lag gaye samajhne mein.**

### Cross-fitted seed model — ek aur leakage guard

Ek aur subtle problem thi.

GraphSAGE ke input mein humein ek feature chahiye tha: **"is account ke padosiyon
ka average risk kitna hai?"** Ye bahut powerful signal hai.

Lekin isko compute karne ke liye humein har account ka risk chahiye. Aur wo risk
ek model se aata hai jo **labels** par train hua hai.

**Problem:** agar wo model poore training set par train hua hai, to account A ka
score uske apne label ko already dekh chuka hai. Aur ab wo score account B ke
feature ke roop mein ja raha hai. **Leakage.**

**Solution — cross-fitting:**

```python
skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
for inner_tr, inner_te in skf.split(train_idx, train_y):
    # fold ke 4/5 par train, 1/5 par predict
```

Har account ka seed risk ek aise model se aata hai jisne **us account ka label
kabhi nahi dekha**.

Ye `_cross_fitted_seed_risk()` mein implement hai.

### Ek bug jo humne pakda — reloaded model alag score deta tha

Ek test hai: `test_engine_roundtrips_through_disk`. Ye model ko save karta hai,
reload karta hai, aur check karta hai ki dono same scores dete hain.

**Ye fail ho gaya.**

Wajah? `seed_model` save hi nahi ho raha tha. Reload par wo `None` hota tha, aur
neighbour-risk feature zero ho jaata tha.

Fix: `self.seed_model` ko `seed_xgb` ke naam se persist kiya.

**Sabak:** agar ye test na hota, to production mein deployed model **training se
alag** behave karta — aur kisi ko pata na chalta.

### Aur ek problem — isotonic ne ties bana diye

Isotonic regression ek **step function** seekhta hai. Iska matlab bahut saare
accounts ko **exactly same** calibrated score mil sakta hai.

Aur jab scores tie ho jaate hain, to **ranking khatam** ho jaati hai. AUC gir
gaya — 0.999 se 0.958.

Fix:

```python
TIE_BREAK = 0.02
p = (1.0 - TIE_BREAK) * calibrated + TIE_BREAK * raw_blend
```

Calibrated score aur raw blend ka ek **convex mix** — 98% calibrated, 2% raw.
Itna chhota ki calibration na bigde, lekin itna ki ties toot jaayein.

### Seekhe hue weights — aur ek imaandaar baat

Training ke baad fusion ne ye weights seekhe:

```
xgb        : 0.5946
graph      : 0.5820
temporal   : 0.0090
intel      : 0.1550
max_logit  : 0.4168
```

**Do observations:**

**1.** `xgb` aur `graph` lagbhag barabar hain. Ye complementarity ka proof hai —
dono equally valuable hain.

**2.** **`temporal` ko sirf 0.009 mila. Lagbhag zero.**

Iska matlab kya? Iska matlab ye ki TGN jo dekh raha tha, wo **zyadatar** XGBoost
aur GraphSAGE pehle se capture kar rahe the. Is dataset par wo **redundant** hai.

**Hum isko chhupa sakte the.** Slide se hata sakte the. Nahi hata rahe.

Kyunki fusion ka poora point hi yahi hai — wo **naap kar** decide kare, humaari
umeed par nahi chale. Agar humne pehle se weights fix kar diye hote (jaise
`prior_weights` mein 0.20 temporal), to hum ek redundant signal ko 20% weight
de rahe hote.

**Aur ek honest note:** ye is dataset ke liye sach hai. Ho sakta hai asli bank
data mein — jahan sequences lambi aur zyada varied hoti hain — TGN kaafi zyada
contribute kare. Isiliye humne use hataya nahi.

---

<a name="chapter-9"></a>
## Chapter 9 — The Verdict: saare results

### Per-layer performance

| Layer | ROC-AUC | PR-AUC |
|---|---|---|
| XGBoost (L4) | 0.9946 | 0.9540 |
| GraphSAGE (L5) | 0.9962 | 0.9481 |
| TGN (L6) | 0.9628 | 0.8036 |
| Intelligence | 0.6883 | 0.3774 |
| **Fused (L7)** | **0.9966** | **0.9707** |

**Sabse important observation:** Fused model **har individual layer se behtar**
hai — dono metrics par.

Ye complementarity ka **measured proof** hai. Agar layers redundant hote, to
fusion best single layer ke barabar rukta.

**PR-AUC par zyada dhyan dijiye, ROC-AUC par kam.** Kyunki 1.26% base rate par
ROC-AUC optimistic dikhta hai. PR-AUC batata hai ki jab aap actually alert
raise karte hain, tab kya milta hai. Aur wahan fusion ka gain sabse bada hai —
0.9540 se 0.9707.

### Rule engine ke against — same recall par

**Comparison honest hona chahiye.** Agar aap recall match nahi karte, to koi
bhi system keh sakta hai "maine zyada pakda" — bas zyada alert nikaal kar.

To — **same recall, 84.77%** par:

| | Rule engine | BODHI |
|---|---|---|
| Alerts raised | **9,647** | **129** |
| True positives | 128 | 128 |
| False positives | **9,519** | **1** |
| Precision | **1.33%** | **99.22%** |

**99.989% false positives eliminated. 74.78× precision uplift.**

Wahi 128 true positives. Analyst 9,647 case ki jagah **129** case kholta hai.

### Alag thresholds par kya milta hai

| Threshold | Alerts | Precision | Recall | F1 |
|---|---|---|---|---|
| 40 (ALERT) | 238 | 61.76% | **97.35%** | 0.756 |
| 55 | 144 | 97.92% | 93.38% | 0.956 |
| 65 (HIGH) | 144 | 97.92% | 93.38% | 0.956 |
| 85 (CRITICAL) | 143 | 98.60% | 93.38% | 0.959 |

**Ye table operationally sabse useful hai.**

Threshold 40 par: 238 alerts, **97.35% recall** — yaani lagbhag saare mules
pakde ja rahe hain, aur ek analyst 238 case ek din mein dekh sakta hai.

Threshold 85 par: 143 alerts, 98.60% precision — yahan automation act kar sakti
hai kyunki galti ka chance 1.4% hai.

**Do alag operating points, do alag purposes.** Yahi risk-based approach hai.

### Test 1 — Out of time (sabse important generalisation test)

Humne 3 rings aise rakhe jo simulation ke **aakhri window** mein active hue —
training ke baad. Model ne unhe kabhi dekha hi nahi.

| | |
|---|---|
| Late rings | 3 |
| Un rings ke mules | 26 |
| Detected | **26** |
| Recall | **100%** |
| Median score | **100 / 100** |

**Ye batata hai ki model ne pattern seekha hai, specific accounts yaad nahi
kiye.**

### Test 2 — Independence from tickets (strategically sabse important)

Ye slide sabse zyada matter karti hai.

| | |
|---|---|
| Total mules | 151 |
| Kisi ticket mein named | **53** |
| **Kabhi report hi nahi hue** | **98** |
| Model ne detect kiye | 147 |
| Unme se jo kisi ticket mein nahi the | **94** |
| **Detections ka share jo ticket mein nahi tha** | **63.95%** |
| **Never-reported mules par recall** | **95.92%** |

**Iska matlab kya hai?**

Hamari **64% detections complaint ka intezaar nahi kar rahi thi**.

Aur jin 98 accounts ke baare mein kisi ne kabhi complaint nahi ki — un par bhi
model ne **95.92% recall** achieve kiya.

**Ye is project ka sabse bada strategic point hai.** Agar aapka system tickets
follow karta hai, to aap hamesha 36 ghante peeche hain. Ye system tickets ko ek
**input** ki tarah use karta hai, **trigger** ki tarah nahi.

### Test 3 — Lead time

| | |
|---|---|
| Eligible mules | 27 |
| Detected within grid | 27 |
| Jinka ticket bhi tha aur detection bhi | 11 |
| **Ticket se pehle detect hue** | **6 (54.55%)** |
| **Median lead time** | **11.47 ghante** |
| Mean lead time | 28.38 ghante |
| P90 lead time | 128.76 ghante |
| **Detect hue lekin kabhi ticket hi nahi aaya** | **16** |

**Median 11.47 ghante ka matlab:** aadhe cases mein hum complaint se **gyarah
ghante pehle** jaan gaye. Aur us window mein paisa abhi cash nahi bana hota.

**Ek honest note:** ye number **cut spacing** (91.6 ghante) se limited hai —
hum har 91.6 ghante par re-score karte hain evaluation mein. Agar continuously
score karein to lead time aur behtar hoga. Ye evaluation ki limitation hai,
model ki nahi — aur ye `evaluation.json` mein likha hua hai.

### Test 4 — Calibration

| | |
|---|---|
| Expected Calibration Error | **0.0047** |
| Brier score | **0.00125** |

Aur bins dekh lijiye:

| Predicted | Observed | n |
|---|---|---|
| 0.0033 | 0.0003 | 11,755 |
| 0.8038 | 0.9375 | 32 |
| 0.9992 | 1.0000 | 111 |

Top bin mein 111 accounts ko 0.9992 kaha, aur **111 ke 111 mule nikle**.

### Test 5 — Latency

| | |
|---|---|
| Samples | 2,000 |
| Mean | 0.060 ms |
| **p50** | **0.0542 ms** |
| p95 | 0.0944 ms |
| **p99** | **0.1132 ms** |
| Max | 0.4364 ms |
| **Throughput** | **16,677 decisions/second** |

**Ye kaise possible hai?**

Architecture ko **do hisson mein toda**:

1. **Batch path** — L3 (graph), L5 (GraphSAGE), L6 (TGN) schedule par chalte
   hain aur har account ka ek **standing risk** cache karte hain
2. **Inline path** — authorization ke waqt hum sirf us cached score ko aur
   transaction ke apne attributes ko dekhte hain. **Graph traverse nahi karte.**

UPI ki latency budget mein rehne ka **yahi ek tareeka** hai. Agar aap inline
graph traverse karenge, to aap kabhi 100 ms se neeche nahi aayenge.

### Test 6 — Typology-wise recall (kahan kamzor hain)

| Typology / Role | Recall | Detected/Total |
|---|---|---|
| APK_HARVEST | **100%** | 15/15 |
| DEVICE_FARM | **100%** | 31/31 |
| DORMANT_BURST | **100%** | 13/13 |
| role: RELAY | **100%** | 53/53 |
| LAYERING_CHAIN | 98.3% | 58/59 |
| role: COLLECTOR | 98.3% | 59/60 |
| FAN_IN_AGGREGATION | 93.3% | 14/15 |
| role: CASHOUT | 92.1% | 35/38 |
| **SMURFING** | **88.9%** | **16/18** |

**Hum ye table isliye dete hain kyunki average blind spots chhupa deta hai.**

Sabse kamzor — **SMURFING, 88.9%**. Kyun? Kyunki structuring **jaanbujhkar
har threshold ke neeche** rehta hai. Har individual transaction bilkul normal
dikhta hai. Sirf poore pattern se pata chalta hai — aur agar pattern kam samay
mein failaya gaya ho, to signal patla hota hai.

**CASHOUT 92.1%** — kyunki cash withdrawal **crime nahi hai**. Lakhon log roz
karte hain. Sirf inflow ke context mein wo suspicious banta hai.

---

<a name="chapter-10"></a>
## Chapter 10 — The Explanation: model ne 87 kyun diya

### Kyun ye optional nahi hai

Model ne ek account ko 87 diya. **Kyun?**

Agar iska jawaab nahi hai, to ye system ek bank mein deploy **nahi ho sakta**.

Kyunki:
- RBI ke saamne "model ne bola" defence nahi hai
- Investigator bina reason ke case aage nahi badha sakta
- STR mein "grounds of suspicion" section **legally required** hai
- Aur agar customer challenge kare, to bank ko justify karna padega

### Do explanations, kyunki ek se kaam nahi chalta

**1. TreeSHAP — kaunse FEATURES**

Ye exact Shapley values deta hai, approximate nahi. `xgboost` ke
`pred_contribs=True` se.

**Iski key property:** saare attributions **jodkar theek model ke margin ke
barabar** aate hain.

```
Σ φᵢ + φ₀ = model output (log-odds mein)
```

Ye guarantee important hai — matlab explanation **complete** hai, koi hissa
"bacha hua" nahi hai.

Output kuch aisa:

```
rapid_passthrough_ratio = 0.94   →  +2.31
unique_payers = 47               →  +1.87
night_frac = 0.71                →  +1.42
reciprocity = 0.00               →  +0.98
account_age_days = 34            →  +0.61
```

**2. GNNExplainer — kaunse RISHTE**

SHAP ek sawaal ka jawaab **structurally de hi nahi sakta** — *"kaunse rishton ne
score banaya?"*

Kyunki SHAP features par kaam karta hai, edges par nahi.

GNNExplainer graph ke edges par ek **mask** seekhta hai — har edge ko ek weight
[0,1] deta hai — aur optimise karta hai:

> "kam se kam edges rakho, lekin prediction utni hi rehni chahiye"

Output: *"is account ka score in 5 edges ki wajah se hai — teen suspicious
counterparties aur ek shared device."*

### Ek bug jo humne pakda — aur ye batata hai ki metrics jhooth bol sakte hain

Shuruat mein mask ko **1.0 par initialise** kiya tha (sab edges "on").

Result: mask **kabhi move hi nahi karta tha**. 99.4% edges "retained" report
hoti thi. Explanation bekaar tha.

**Kyun?** Kyunki sigmoid ke input par 1.0 daalne se aap **saturated region**
mein hain. Wahan gradient lagbhag zero hota hai. Optimiser ko koi signal hi
nahi mila.

**Fix:**

```python
mask = rng.normal(0.0, 0.1, size=n_edges)
```

Ab mask sigmoid ke **linear region** mein shuru hota hai, jahan gradients strong
hain. Plus normalised fidelity gradient.

**Aur ek honest metric add kiya** — `self_feature_share`.

Ye batata hai ki score ka kitna hissa account ke **apne** features se aaya aur
kitna **graph structure** se. Kyunki kai baar graph ka contribution chhota hota
hai, aur ye chhupana galat hoga.

Teen metrics ab report hote hain:
- `necessity` — ye edges hatane se score kitna girta hai
- `sufficiency` — sirf ye edges rakhne se score kitna bachta hai
- `self_feature_share` — kitna graph se aaya hi nahi

### Narrative generation

`bodhi/explain/narrative.py` — SHAP values aur edge masks ko **AML officer ki
bhasha** mein translate karta hai.

Feature name `rapid_passthrough_ratio = 0.94` ban jaata hai:

> *"94% of credits received left the account within 60 minutes, consistent with
> a pass-through relay rather than a beneficiary account."*

**Kyun ye zaroori hai?** Kyunki STR ek **legal document** hai. Usme
`rapid_passthrough_ratio` likhna kaam nahi karega.

### STR aur CTR

**STR — Suspicious Transaction Report**

FIU-IND ko file hota hai. Hamara system ise **exactly usi evidence se** populate
karta hai jo investigator ne screen par dekhi.

**Ye deliberate hai.** Matlab jo model ne assert kiya, aur jo institution ne
file kiya — wo **same** hai, alag ho hi nahi sakta.

Aur har draft explicitly marked hai: **"requires human sign-off"**. Kuch bhi
autonomously file nahi hota.

`str_filing_days = 7` — detection ke 7 din ke andar file karna hai.

**CTR — Cash Transaction Report**

Threshold ₹10,00,000 (PMLA).

**Aur yahan ek critical detail hai:** hum **per account per calendar day**
aggregate karte hain. Per transaction **nahi**.

**Kyun?** Kyunki obligation us level par lagti hai. Agar koi ₹3 lakh × 4 baar
ek hi din mein nikale, to wo ₹12 lakh hai — reportable. Per-transaction check
usse miss kar dega.

**Aur theek isiliye structuring per-transaction check ko haraa deti hai.**

*(Ek test failure ki kahani: ek test isliye fail hua kyunki test ke timestamps
aadhi raat ke aar-paar the — do alag calendar days mein. Code sahi tha, test
galat tha. Humne fixture theek kiya aur ek **doosra test** add kiya jo
specifically assert karta hai ki midnight par split hona chahiye.)*

---

<a name="chapter-11"></a>
## Chapter 11 — The Red Button: kill-switch aur audit

### Sabse sensitive hissa

Account freeze karna is system ka sabse consequential action hai.

Code ke docstring mein literally likha hai:

> *"A wrongly frozen account is somebody unable to pay for medicine, and no AUC
> improvement justifies being casual about it."*

Isliye kill-switch ek **threshold nahi** hai. Wo **constraints ka set** hai.

### Paanch constraints

**1. Proportionality**

Actions ki ek ladder hai, ascending severity:

```
MONITOR → STEP_UP_AUTH → HOLD_CREDIT → FREEZE_DEBIT → FULL_FREEZE
```

**85 se neeche automation freeze kar hi nahi sakti.** Wo sirf inbound credit hold
kar sakti hai ya step-up authentication force kar sakti hai.

Full freeze **sirf CRITICAL band** ke liye reserved hai.

**2. Corroboration**

> Model score akela **kabhi** highest action trigger nahi karta. Kam se kam
> **do independent channels** ko agree karna padega.

Chaar channels hain: behaviour, graph, temporal, external intelligence.

**Kyun?** Kyunki ek layer ka failure mode kisi ko freeze nahi kar sakna
chahiye. Agar XGBoost mein koi bug aa jaye, to graph model usko rok dega.

**3. Reversibility + TTL**

Har action ka ek expiry hai:

```python
DEFAULT_TTL_HOURS = {
    MONITOR:       24 * 30,   # 30 din
    STEP_UP_AUTH:  72,        # 3 din
    HOLD_CREDIT:   24,        # 1 din
    FREEZE_DEBIT:  48,        # 2 din
    FULL_FREEZE:   24,        # 1 din  ← sabse severe, sabse chhota TTL
}
```

**Dhyan dijiye — `FULL_FREEZE` ka TTL sabse chhota hai (24 ghante).**

Ye deliberate hai. Jitna severe action, utni jaldi human review chahiye. 24
ghante baad wo automatically revert ho jaayega jab tak koi human renew na kare.

Aur revert karna khud ek **audited event** hai.

**4. Rate limiting**

Per window automated freezes bounded hain.

**Kyun?** Do scenarios:
- Model degrade ho jaye
- Upstream feed **poison** ho jaye (koi attacker jaanbujhkar fake tickets bhej de)

Dono cases mein blast radius **capped** rehta hai. Baaki queue humans ke paas
chali jaati hai.

**5. Protected accounts**

Salary, pension aur government-benefit accounts automated freezing se **poori
tarah bahar** hain. Wo hamesha human ke paas jaate hain.

**Kyun?** Kyunki in accounts par log **survive** karte hain. Ek pension account
freeze karne ka matlab hai kisi budhe insaan ka mahina barbaad karna.

### Aur sabse important design choice — downgrade, escalate nahi

Jab koi constraint fire karta hai, to action **downgrade** hota hai — escalate
nahi — aur human review ke liye flag ho jaata hai.

**Matlab system ka default "na" hai, "haan" nahi.**

Main ye zor dekar kehna chahta hoon: **ye system "na" keh sakta hai.** Aur mere
hisaab se ek AI system ki sabse important capability yahi hai.

### Audit log — hash chain

`bodhi/compliance/audit.py`.

Har decision — **har refusal included** — ek append-only JSONL file mein likha
jaata hai. Aur har entry mein pichhli entry ka **hash** hota hai:

```
entry_n.prev_hash = SHA256(entry_{n-1})
```

**Iska matlab kya?**

Agar koi entry #47 ko badalna chahe, to entry #48 ka `prev_hash` match nahi
karega. Aur #48 ko theek karne ke liye #49 badalna padega. Aur aage tak.

**Tampering mathematically detectable ho jaati hai.**

Head hash **exposed** hota hai — taaki use kisi external system mein anchor kiya
ja sake.

**Honest limitation:** abhi hum ise kahin externally anchor **nahi** karte. Ye
`docs/DEPLOYMENT.md` mein likha hua hai. Ek asli deployment mein ye hash roz
kisi external timestamping service ya doosre system mein bhejni chahiye.

### PII pseudonymisation

`bodhi/compliance/pii.py` — keyed-HMAC.

Account numbers, VPAs, phone numbers — sab HMAC se pseudonymise hote hain jab wo
logs mein ya exports mein jaate hain.

**HMAC kyun, plain hash kyun nahi?**

Kyunki plain SHA256 **brute-forceable** hai. Account numbers ka space chhota hai
— aap saare possible account numbers hash karke rainbow table bana sakte hain.

HMAC mein ek **secret key** hoti hai. Bina key ke reverse karna practically
impossible hai.

**Aur "keyed" ka ek aur fayda:** aap key rotate kar sakte hain, aur purane
pseudonyms invalid ho jaate hain.

---

<a name="chapter-12"></a>
## Chapter 12 — SHIELD: malicious APK triage

### Ye kyun exist karta hai

Yaad hai Chapter 1 mein wo teesra tareeka? **Malicious app.**

Wo trojan APK jo victim ke phone par install hui — uske andar beneficiary
account numbers **hardcoded** hote hain.

Ab socho — agar hum us APK ko **analyse** kar lein **jis din wo appear hoti
hai**, to hum un beneficiary accounts ko **pehle se** jaan sakte hain.

**Paisa move hone se pehle.**

Ye architectural farak hai, incremental nahi.

### Kya implement kiya (aur kya nahi)

Scope deliberate hai, aur hum ise clearly state karte hain.

Proposal mein ek **full dynamic sandbox** tha — Frida hooking, memory dumping.
Wo ek instrumented Android image maangta hai aur self-contained ship nahi ho
sakta.

**Jo implement hai, wo asli hai — mocked nahi:**

**1. Binary AndroidManifest.xml parsing**

Ye sabse technical hissa hai. APK ke andar `AndroidManifest.xml` **plain XML
nahi hota** — wo ek binary format (AXML) mein compiled hota hai.

Humne uska **string pool** spec ke hisaab se parse kiya hai — `struct.unpack`
se, chunk headers padhkar, UTF-8 aur UTF-16 dono encodings handle karke.

Isse milta hai: declared permissions aur package name.

**2. DEX mining**

`classes*.dex` files se regex se extract:
- UPI handles (`name@bank`)
- Bank account numbers
- IFSC codes
- C2 URLs
- Bare IP addresses
- Phone numbers

**Yahi wo financial intelligence hai jo graph mein jaati hai.**

**3. Packer aur obfuscation detection**

Commercial packers ke signatures, aur identifier obfuscation (jab class names
`a`, `b`, `aa` jaise ho jaate hain).

**4. Signing artefacts**

### Sabse smart hissa — permission COMBINATIONS

Ye ek asli insight hai.

Naive approach: "dangerous permissions" ki list banao, jitni zyada utna
suspicious.

**Ye kaam nahi karta.** Kyunki:

- `READ_SMS` akela? Ye ek **messaging app** hai. Bilkul normal.
- `SYSTEM_ALERT_WINDOW` akela? Ye ek **chat head** feature hai. Facebook
  Messenger karta hai.
- `BIND_ACCESSIBILITY_SERVICE` akela? Ye ek **accessibility tool** hai — blind
  users ke liye. Bilkul legitimate.

**Lekin teenon ek saath?**

- `READ_SMS` → OTP padh sakta hai
- `SYSTEM_ALERT_WINDOW` → banking app ke upar fake screen daal sakta hai
- `BIND_ACCESSIBILITY_SERVICE` → screen padh sakta hai aur taps simulate kar
  sakta hai

**Ye ek banking trojan hai.** Definitively.

Isliye scoring **combinations** par hoti hai, individual permissions par nahi.
Har permission ka ek weight hai (`READ_SMS: 7`, `RECEIVE_SMS: 8`), lekin
combination bonuses alag se hain.

### IOC bridge — aur yahi asli value hai

`bodhi/shield/ioc_bridge.py`.

APK se nikla har financial identifier **stream hokar** Mule Hunter graph mein
ek **weighted node** ban jaata hai.

**Consequence:**

Jab ek naya trojan analyse hota hai — **us din jab wo appear hota hai** — to wo
beneficiary accounts jinke liye wo trojan banaya gaya tha, unka intelligence
score **pehle se elevated** ho jaata hai.

**Pehle victim ke install karne se pehle.**

Detection **retrospective hona band** ho jaata hai.

Aur hamare results mein ye dikhta hai: `APK_HARVEST` typology par recall
**100%** (15/15).

---

<a name="chapter-13"></a>
## Chapter 13 — Their Dataset: organisers ka apna data

### Situation

Organisers ne humein apne Phase-2 dataset ka **column dictionary** diya —
`Description.xlsx` — aur kaha:

> *"Your model will be evaluated based on the validation data set we have not
> shared with you. Performance on the validation data set will have a lot of
> weightages."*

To humne ek **doosri pipeline** banayi, seedhe unke schema par — `bodhi/boi/`.

### Ye dataset alag cheez hai

**3,924 columns.** 3,923 predictors + `FRAUD_TGT` label.

Aur sabse important — **ek row ek ALERT hai, ek account nahi.**

Iska matlab problem badal jaati hai:
- Population pehle se bank ke rules se **filter** ho chuki hai
- Base rate bahut zyada hai (raw population ke ~1% ke muqable)
- Kaam detection nahi, **triage** hai

### Column grammar — humne parse kiya, treat nahi kiya

Lagbhag har predictor ek compact grammar se machine-generated hai:

```
[aggregation] _ [CI|BI] _ [channel] _ [CR|DB] _ [TXN|AMT|BAL] _ [window]

RA_CI_NON_CASH_CHQ_TXN_CR_L7_31D
│  │   │            │   │   └── last 7 days vs last 31 days
│  │   │            │   └────── transaction count
│  │   │            └────────── sirf credits
│  │   └─────────────────────── non-cash, non-cheque
│  └─────────────────────────── customer induced (vs BI = bank induced)
└────────────────────────────── ratio of averages
```

`bodhi/boi/schema.py` is grammar ko **parse** karta hai — names ko opaque nahi
maanta.

**Kyun ye zaroori hai?**

1. 3,900 columns ko ~1,300 **families** mein group kar sakte hain
2. Pata chalta hai ki `NON_CASH_CHQ` ko kabhi `CASH` nahi padhna chahiye
3. Customer behaviour aur bank-induced postings (fees, GST) mein farak kar
   sakte hain

**Vocabulary jo mili:**

| Dimension | Values |
|---|---|
| Aggregations | `R`, `RA`, `D`, `DA`, `D_TA`, `MIN`, `MAX`, `AVG`, `TOT`, `CNT` |
| Channels | 17 — `CASH`, `CHQ`, `NON_CASH_CHQ`, `UPI`, `ELEC_XFER`, `NET_BNKING`, `ATM`, `POS_PYMT`, `BBPS`, `APB`, `LOAN`, `STDNG_INSTR`, `FEES_CHRGS`, `GST` |
| Windows | `L7D`, `L14D`, `L31D`, `L7_14D`, `L14_31D`, `L7_31D`, plus `_OCC` variants |
| Static block | 33 columns — demographics, alert metadata, risk flags |

*(Ek galti jo humne ki: pehla parser vocabulary **guess** karke likha tha, aur
usne `FEES_CHRGS`, `GST`, `MBNKING`, `BI`, `_OCC` suffix aur `14_31D` window
miss kar diye. Fix: vocabulary ko **data-driven** banaya — dictionary se hi
extract kiya.)*

### Finding 1 — chaar columns mein jawaab chhupa hai

`FRAUD_SUSPECTED`, `FALSE_POSITIVE`, `OTHER_RESOLUTION`, `UNATTENDED`.

Dictionary inhe **"Resolution status flag"** kehti hai.

**Matlab: analyst ne alert ko KAISE BAND KIYA.**

Aur `MIN_RESOLVE_DAYS` / `MAX_RESOLVE_DAYS` — kitna time laga.

**Ek KHULA alert — jise score karna hai — usme ye hote hi nahi.**

Humne naapa ki inhe include karne se kya milta hai:

| | Strategy | CV PR-AUC | Held-out ROC-AUC |
|---|---|---|---|
| Resolution columns **quarantined** | `bank_finalized` | **0.177** | 0.705 |
| Resolution columns **included** | `all` | **0.972** | 0.998 |

**5.5× jump.** Aur akela `FRAUD_SUSPECTED` poore gain ka **33%**. Chhe leakage
columns top paanch importance slots mein.

**Ye model nahi hai. Ye doosre column mein likha hua jawaab dekh lena hai.**

Humne inhe **default quarantine** kar diya. `--allow-leakage` flag difference
measure karta hai, aur karte waqt **warning print** karta hai.

**Aur ek imaandaar statement:** agar organisers ki validation file mein bhi ye
columns hain, to unhe use karne wala model **spectacular** score karega aur uska
koi matlab nahi hoga. Hum honest number submit karna pasand karenge, aur ye
likh kar denge.

### Finding 2 — bank ke apne 18 features jeet gaye

Dictionary mein 3,923 mein se **18 predictors** `Bank_Finalized_Variables` mark
hain. Ye domain knowledge hai — un logon ki jo is data ko jaante hain.

Humne ise **instruction nahi, hypothesis** maana aur test kiya.

Chaar strategies, repeated stratified cross-validation:

| Strategy | Features | CV ROC-AUC | CV PR-AUC |
|---|---|---|---|
| **`bank_finalized`** | **18** | **0.712** | **0.177** |
| `bank_plus_engineered` | 2,028 | 0.651 | 0.163 |
| `auto_topk` | 131 | 0.622 | 0.137 |
| `all` | 5,926 | 0.618 | 0.112 |

**18 expert-chosen columns ne saare 5,926 ko haraya.** Aur automatic selection
ko bhi.

**Kyun?** Ye theory bhi predict karti hai. Jab positives kuch sau hain (186) aur
predictors hazaaron, to har extra feature noise fit karne ka ek aur mauka hai.
Isko **curse of dimensionality** kehte hain.

**Domain knowledge ne brute force ko haraya.**

### Aur sabse important — humne khud ko dhokha nahi diya

**Feature selection har fold ke ANDAR chalti hai.**

Ye sabse common mistake hai machine learning mein: poore training set par 3,900
columns rank karo, top 100 chuno, phir cross-validate karo.

**Ye ek shandaar score manufacture karta hai jo held-out data par ud jaata hai.**

Kyunki selection ne already poora data dekh liya hai.

Hamare code mein `BOIModel._select()` har CV fold ke **andar** call hota hai —
sirf us fold ke training rows par.

**Aur iske liye ek test hai:**

`test_cross_validation_is_not_inflated_by_selection` — ye target ko **shuffle**
kar deta hai (saara signal khatam), aur assert karta hai ki reported AUC chance
ke aas-paas rahe (< 0.62).

Ek globally-selecting pipeline is test par **0.5 se kaafi upar** return karti
hai. Ye test hi wo guard hai.

**Aur ek aur proof:** untouched holdout ne **0.7046** score kiya, jabki CV
estimate **0.7052** tha. Gap **0.0006**. Itna chhota gap hi evidence hai ki
selection procedure honest hai.

### Engineered features — aur ek honest result

Humne kuch cross-column features banaye (`BX_` prefix ke saath):

- **Family roll-ups** — same measurement ke 7/14/31 din versions ka mean, max,
  std, null-count, plus **acceleration ratio** (short vs long window)
- **Channel concentration** — Herfindahl index, entropy, top-channel share.
  *Kyun?* Kyunki **ek mule funnel karta hai** — andar UPI se, bahar cash se. Koi
  supplied column ye nahi dekh sakta, kyunki har column ek channel tak scoped
  hai.
- **Customer- vs bank-induced share** — jis account ki saari activity fees aur
  GST postings hai, wo us tarah se dormant hai jo matter karta hai
- **Credit/debit balance** — ek pass-through account lagbhag utna hi debit karta
  hai jitna credit
- **Missingness structure** — bank extract mein **kaunse blocks null hain** ye
  khud informative hai

**Result:** in features ne stand-in data par bank ke 18 ko **nahi haraya**
(0.651 vs 0.712).

**Humne inhe rakha kyun?** Kyunki ye comparison **data-dependent** hai. Asli
file aane par ye dobara chalega, aur ho sakta hai result badal jaye.

### Submission day robustness

Sabse zyada chance failure ka **model nahi — file** hai. Loader (`dataset.py`)
ye sab handle karta hai:

| Hazard | Handling |
|---|---|
| csv / tsv / parquet / xlsx | suffix se detect |
| Columns alag order mein | **dictionary** ke against align (training file ke nahi) |
| Columns missing | all-NaN reinsert taaki matrix shape stable rahe; reported |
| Extra unexpected columns | reported aur ignored |
| Numbers text mein (`1,234.50`, `(2,000.00)`, `45%`) | coerce; reported |
| `NULL` / `N/A` / `-` / `#N/A` | missing treat |
| Target column absent | expected — yahi validation case hai |
| All-null ya constant columns | dropped, reported |

**Dictionary ke against align kyun, training file ke nahi?**

Kyunki agar validation extract mein ek extra column aa gaya, to training file ke
against align karne se **sab kuch shift** ho jaata — chupchaap. Dictionary ek
stable reference hai.

### Aur sabse imaandaar disclosure

**Ye saare numbers organisers ke ASLI data par nahi hain.** Wo data release hi
nahi hua tha.

Ye ek **stand-in table** par hain jo `bodhi/boi/synth.py` generate karti hai —
unke **exact 3,924 columns** ke saath, har column ke grammar se implied
distribution ke saath, aur ek **deliberately modest** injected signal ke saath.

Ye batate hain ki:
- Pipeline end-to-end chalti hai
- Methodology khud ko inflate nahi karti
- Leakage trap asli hai

**Ye model performance NAHI hain, aur hum inhe waisa present nahi karenge.**

Jab asli file aayegi:

```bash
python scripts/boi_train.py --train <unki file>
```

— aur is page ka har number ek **measured** number se replace ho jayega.

---

<a name="chapter-14"></a>
## Chapter 14 — The Machine Room: API, dashboard, tests, deployment

### API — 22 routes, ek process

`bodhi/api/main.py` — FastAPI. Do audiences, ek process:

- `/api/*` — machine interface jo ek core banking system call karega
- `/` — investigator dashboard

**Kyun ek saath?** Code ke comment mein likha hai: *"Keeping them together means
the demo cannot drift from the API."* Jo dashboard dikhata hai, wo **wahi** data
hai jo API deti hai.

**Main routes:**

| Route | Kaam |
|---|---|
| `GET /api/health` | status, models_loaded, accounts_in_graph |
| `GET /api/overview` | population, bands, alerts, exposure |
| `GET /api/metrics` | wahi numbers jo report cite karti hai |
| `GET /api/alerts` | alert queue |
| `GET /api/accounts/{id}` | ek account ka poora profile |
| `GET /api/accounts/{id}/graph` | uska neighbourhood |
| `GET /api/accounts/{id}/flows` | time-respecting money trail |
| `GET /api/accounts/{id}/gnn-explain` | edge importance |
| `GET /api/rings` | detected rings |
| `POST /api/score/transaction` | **inline scoring** — ye production path hai |
| `POST /api/ingest` | batch transactions |
| `POST /api/shield/analyze` | APK upload aur triage |
| `POST /api/actions/killswitch` | containment action |
| `POST /api/actions/revert` | undo |
| `GET /api/reports/str/{alert_id}` | STR draft |
| `GET /api/reports/ctr` | CTR candidates |
| `GET /api/audit` | audit log |

**Inline scoring ka response:**

```json
{
  "txn_id": "T1",
  "decision": "ALLOW",
  "risk_score": 0.0,
  "band": "SAFE",
  "reasons": [],
  "src_risk": 0.0,
  "dst_risk": 0.0,
  "latency_ms": 0.404,
  "scored_at": "2026-08-17T13:24:08Z"
}
```

Chaar decisions: **ALLOW / REVIEW / HOLD / BLOCK**.

### Dashboard

`dashboard/` — plain HTML, CSS, ek JS file. Koi React nahi, koi build step nahi.

**Kyun?** Kyunki ek build step ek aur cheez hai jo submission day par tut sakti
hai. Aur ye dashboard ek investigator console hai, ek SPA product nahi.

Tabs: Overview, Alerts, Account detail (graph + flows + SHAP + GNN), Rings,
Score a transaction, SHIELD, Agents, Compliance/Audit.

### Tests — 121, aur ye kya protect karte hain

| File | Tests | Kya protect karta hai |
|---|---|---|
| `test_data_and_features.py` | 14 | Simulator sahi structures banata hai; features point-in-time hain |
| `test_gradients.py` | 3 | **NumPy gradients finite differences se match karte hain** |
| `test_graph_and_models.py` | 19 | Graph construction, model fit/predict/save/load |
| `test_pipeline_and_api.py` | 24 | End-to-end pipeline, API routes, roundtrip through disk |
| `test_shield_and_compliance.py` | 25 | AXML parsing, DEX mining, STR/CTR, audit chain, PII |
| `test_boi_track.py` | 22 (27 collected) | Schema grammar, loader robustness, **selection leakage guard** |
| `test_submission_docs.py` | 6 | Report/deck build hote hain aur sahi content rakhte hain |

**Teen tests jo sabse zyada matter karte hain:**

1. **`test_gradients.py`** — poore neural code ki correctness guarantee
2. **`test_engine_roundtrips_through_disk`** — deployed model training se alag
   behave na kare
3. **`test_cross_validation_is_not_inflated_by_selection`** — methodology khud
   ko dhokha na de

### Deployment — measured facts

*(Poori detail `docs/DEPLOYMENT.md` mein.)*

**RAM aur boot — measured:**

| Baked world | Boot time | Peak RSS |
|---|---|---|
| 6,000 accounts / 90 days (Dockerfile) | **23 s** | **1.13 GB** |
| 12,000 accounts / 120 days (`make all`) | **85 s** | **3.09 GB** |

**512 MB free tier OOM-kill ho jayega.** Minimum 2 GB.

**Aur ek behaviour jo design karna padta hai:**

Bootstrap **lazy** hai — world **pehli request** par load hota hai, process start
par nahi.

Measured: pehla `curl /api/health` — **73 second**. Doosra — **11 millisecond**.

Iska matlab platform ka health check hi wo pehli slow request hai. Grace period
120 s+ chahiye, warna container **restart loop** mein chala jayega.

**Deploy options:**

- **Docker** (recommended) — image build time par data + train bake kar leta hai
- **VM** — systemd + nginx + certbot
- **Render / Railway / Fly.io** — Dockerfile se, 2 GB RAM, grace 120 s
- **Tunnel** (judging ke liye best) — `cloudflared tunnel --url http://localhost:8000`

**Vercel par NAHI chalega** — chaar hard blockers:
1. Bundle limit 250 MB; akela `xgboost` **228 MB** hai (poora set ~720 MB)
2. Memory 1.13–3.09 GB chahiye
3. Har cold invocation poori duniya rebuild karega (23–73 s)
4. Filesystem read-only; audit log aur casebook ko writable directory chahiye

### Reproducibility — sab kuch ek command se

```bash
make setup       # venv + dependencies
make all         # data + train + sample-apk + evaluate
make serve       # API + dashboard on :8000
make test        # 121 tests
make submission  # report PDF/DOCX + deck PPTX/PDF
```

**Aur ek discipline jo main highlight karna chahta hoon:**

Report, deck, aur ye document — **sab `artifacts/metrics/evaluation.json` se
numbers padhte hain**. Koi number type nahi kiya gaya.

Iska matlab: agar aap model retrain karein aur numbers badlein, to `make
submission` chalane se **saare documents update ho jaate hain**.

Documents aur code **kabhi disagree nahi kar sakte.**

---

<a name="chapter-15"></a>
## Chapter 15 — Honest Limits aur The Bigger Game

### Wo cheezein jo hum NAHI kar sakte

Main ye khud bata raha hoon. Kyunki jo prototype apni limitations chhupata hai,
wo deployment team ke kisi kaam ka nahi hai.

**1. Results simulated data par hain.**

Ye architecture ko prove karte hain — ki ye rule engine ko decisively haraata
hai, realistic decoys wale data par. Ye **production performance ka forecast
nahi** hain.

**2. SHIELD sirf static analysis karta hai.**

Dynamic sandbox — Frida hooking, memory dumping — ke liye instrumented Android
image chahiye. Wo self-contained ship nahi ho sakta.

**3. Graph layers real-time nahi hain.**

Poori population ka re-score ~38 second leta hai. To jis account ka
neighbourhood pichhle batch ke baad badla hai, wo **thodi purani structure** par
score ho raha hai.

**4. System EK institution dekhta hai.**

Jo rings kai banks se hokar jaate hain, wo **aadhe hi** dikhte hain.

**Ye data-sharing ki problem hai, modelling ki nahi.** Aur iska solution
technical nahi, policy-level hai — jaise ek shared mule registry.

**5. Cold start.**

Sabse strong single feature hai **neighbourhood risk**, jo confirmed cases se
propagate hota hai.

Iska matlab: ek bilkul naya deployment — jahan koi known mule nahi hai — wahan
performance **kaafi giregi**.

**6. Disparate impact abhi evaluate nahi hua.**

*(Ye mere liye sabse important hai.)*

`kyc_level = MIN_KYC` aur shared-device features **predictive** hain. Model unhe
use karta hai.

Lekin ye features kam-income wale households aur multi-occupancy ghar se
**correlate** karte hain. Ek ghar mein ek phone — ye gareebi ka indicator hai,
crime ka nahi.

Koi bhi asli deployment se pehle **alert-rate parity** naapna zaroori hai —
income band, region, aur KYC tier ke across.

**Hum ye naap nahi paaye. Aur isliye keh rahe hain.**

**7. Audit log externally anchored nahi hai.**

Hash chain hai, lekin head hash kahin bahar publish nahi hoti. Ek asli
deployment mein ye roz kisi external system mein anchor honi chahiye.

**8. API par authentication nahi hai.**

Prototype ke liye deliberate (judge click karke dekhe), public URL ke liye
**unacceptable**. `docs/DEPLOYMENT.md` mein routes ki list aur nginx basic-auth
ka fix diya hua hai.

### Agar hum aage badhaayein — roadmap

**Turant:**
- Background warm-up (health check turant respond kare)
- API key ya OIDC authentication
- Alert-rate parity measurement

**Medium term:**
- Streaming graph updates — taaki graph layer bhi near-real-time ho
- Federated learning — taaki multiple banks bina data share kiye ek saath seekh
  sakein
- Dynamic APK sandbox

**Long term:**
- Multi-bank mule registry (ye policy problem hai, technical nahi)
- Adversarial robustness — jab mule operators ye seekh lenge ki system kya
  dekhta hai, to wo apna behaviour badlenge

### The bigger picture

Ek baat jo main kehna chahta hoon.

Hum ye daawa **nahi** kar rahe ki humne fraud solve kar diya hai. Fraud ek
economic problem hai, ek social problem hai, aur ek technology problem bhi hai —
lekin sirf teesri wali hum address kar rahe hain.

Hum ye keh rahe hain:

Us teacher ke aath lakh rupaye — jo sattaais khaaton mein toote the — un khaaton
ka **shape aur unki sangat**, complaint aane se pehle, machine ko **dikh sakti
thi**.

Aur agar wo dikh sakti hai, to us par act bhi kiya ja sakta hai.

Ek rule engine wo nahi dekh sakta tha — isliye nahi ki wo kharaab tha, balki
isliye ki uske paas **wo baat kehne ki bhasha hi nahi thi**.

Teen complementary aankhein — behaviour, network, aur time — ek monotonicity
constraint ke andar ek calibrated score mein jodi jaayein, to false positives
**99.98%** gir jaate hain, **wahi** recall par. Explanations itne specific hote
hain ki file kiye ja sakein. Aur containment itna cautious hota hai ki automate
kiya ja sake.

Ye poora system — engine, simulator, dashboard, aur wo har script jisse ye
saare numbers dobara paida kiye ja sakte hain — **121 tests** ke saath open
source release ho chuka hai.

---

## Appendix A — Har number ek jagah

### Dataset

| Metric | Value |
|---|---|
| Accounts | 12,000 |
| Transactions | 8,34,738 |
| Mule accounts | 151 (1.26%) |
| Fraud transactions | 2,121 (0.25%) |
| Rings | 21 |
| Government tickets | 281 |
| IOCs | 30 |
| Simulation window | 120 din (3 Mar – 1 Jul 2026) |
| Late rings (out-of-time) | 3 |
| Seed | 7 |

### Splits

| Fold | Size | Kaam |
|---|---|---|
| train | 6,000 | Base models fit |
| val | 1,800 | Early stopping |
| fuse | 1,800 | Stacker fit (base models ne nahi dekha) |
| test | 2,400 | Ek baar, end mein |

### Models

| Layer | ROC-AUC | PR-AUC | Fusion weight |
|---|---|---|---|
| XGBoost | 0.9946 | 0.9540 | 0.5946 |
| GraphSAGE | 0.9962 | 0.9481 | 0.5820 |
| TGN | 0.9628 | 0.8036 | 0.0090 |
| Intelligence | 0.6883 | 0.3774 | 0.1550 |
| max_logit | — | — | 0.4168 |
| **Fused** | **0.9966** | **0.9707** | — |

### Headline results

| Metric | Value |
|---|---|
| Rule engine alerts | 9,647 @ 1.33% precision |
| BODHI @ same recall | 129 @ 99.22% precision |
| False positives removed | **99.989%** |
| Precision uplift | **74.78×** |
| Out-of-time recall | **100%** (26/26) |
| Detections never in any ticket | **63.95%** |
| Recall on never-reported mules | **95.92%** |
| Median lead time | 11.47 ghante |
| ECE | 0.0047 |
| Brier | 0.00125 |
| Latency p50 / p99 | 0.054 ms / 0.113 ms |
| Throughput | 16,677/sec |
| Training time (all layers) | 78.76 s |

### BOI track (stand-in data)

| Metric | Value |
|---|---|
| Declared columns | 3,924 |
| Bank-finalized features | 18 |
| Best CV ROC-AUC | 0.712 (bank_finalized) |
| All columns CV ROC-AUC | 0.618 (5,926 features) |
| Held-out ROC-AUC | 0.7046 (CV: 0.7052) |
| PR-AUC without leakage | 0.177 |
| PR-AUC with leakage | 0.972 (**5.5×**) |

---

## Appendix B — 20 sawaal aur unke jawaab

**1. Synthetic data par ye numbers ka kya matlab hai?**
Hard negatives ke FP rates dekhiye — BC agents 0.33%, collectors 2.80%. Humne
apne model ko todne ki koshish ki. Architecture prove hua hai, production number
nahi — aur ye report mein likha hai.

**2. Aapne baseline jaanbujhkar kharaab banaya?**
Nahi. Har rule ka individual precision report kiya hai —
`R07_NEW_ACCOUNT_TURNOVER` ka 29.87% hai, jo achha hai. Problem tab hoti hai jab
aath rules `OR` se judte hain.

**3. Deep learning framework kyun nahi?**
Do wajah: environment mein PyTorch blocked tha, aur ab engine bina GPU/CUDA ke
laptop par chalta hai — jo bank deployment ke liye bada fayda hai. Gradients
finite differences se verify kiye hain.

**4. TGN ka weight 0.009 hai — matlab wo bekaar hai?**
Is dataset par wo largely redundant hai — XGBoost aur GraphSAGE ne wo signal
pehle se capture kar liya. Humne ise chhupaya nahi. Asli bank data par result
alag ho sakta hai, isliye hataya nahi.

**5. Chaar folds kyun, teen nahi?**
Kyunki validation fold ne base models ko early-stop kiya, to us fold par unke
scores optimistically biased hain. Stacker unpar fit hoga to over-trust karega.
Isliye ek dedicated `fuse` fold.

**6. Agar model galat freeze kar de?**
Constraints: 85 se neeche freeze nahi, do independent layers ka agreement zaroori,
TTL (FULL_FREEZE sirf 24 ghante), reversible, rate-limited, salary/pension
accounts exempt. Aur har refusal audit log mein.

**7. Real bank mein deploy kaise hoga?**
Docker, 2 GB RAM, ek container, 23 second boot, koi GPU nahi. Batch graph scoring
+ inline cached decision ka split. `docs/DEPLOYMENT.md` mein poori detail.

**8. Cold start kaise handle karenge?**
Abhi nahi kar sakte — ye ek stated limitation hai. Sabse strong feature
neighbourhood risk hai jo confirmed cases se aata hai. Practical approach: pehle
rule engine + manual investigation se seed cases banao, phir model deploy karo.

**9. Kya ye multiple banks par kaam karega?**
Abhi nahi. Ek institution dekhta hai. Cross-bank rings aadhe dikhte hain. Ye
data-sharing problem hai — solution federated learning ya ek shared mule
registry hai.

**10. Bias ka kya?**
`MIN_KYC` aur shared-device features predictive hain lekin low-income households
se correlate karte hain. Alert-rate parity **naapa nahi gaya hai**, aur koi bhi
deployment se pehle naapna zaroori hai. Ye report mein explicitly likha hai.

**11. Explainability regulator ke liye kaafi hai?**
TreeSHAP exact hai (attributions margin ke barabar jodte hain), GNNExplainer
edges par mask deta hai, aur dono ka output STR ke grounds-of-suspicion mein
jaata hai. Jo screen par dikha aur jo file hua — same.

**12. Organisers ke data par kya karenge?**
`python scripts/boi_train.py --train <file>`. Pipeline unke exact schema par
build hai. Resolution columns default quarantined hain.

**13. Leakage columns use kar lein to score badh jayega na?**
Haan — PR-AUC 0.177 se 0.972. Lekin wo model nahi hai. Ek khula alert mein wo
columns hote hi nahi. Hum honest number denge aur ye reasoning likh kar denge.

**14. Latency 0.054 ms — ye believable hai?**
Kyunki inline path graph traverse nahi karta. Graph aur temporal layers batch
mein chalte hain aur ek standing risk cache karte hain. Inline decision sirf
cached score + transaction attributes dekhta hai.

**15. Kitna data chahiye train karne ke liye?**
Hamare setup mein 12,000 accounts / 8.3 lakh transactions / 151 mules. Positives
ki sankhya sabse zyada matter karti hai — 151 already kam hai, aur isiliye humne
regularisation aggressive rakhi (`max_depth=6`, `min_child_weight=3`).

**16. Training kitna time leta hai?**
78.76 second, saari layers, ek CPU par. Data generation 5 second.

**17. Kya isko real-time kar sakte hain?**
Inline decision already real-time hai (0.054 ms). Graph layers batch hain (~38 s
full re-score). Streaming graph updates roadmap par hain.

**18. `max_scale_pos_weight = 12` kyun cap kiya?**
Kyunki high scale_pos_weight calibration bigaad deta hai. Aur hamein calibration
chahiye kyunki 85 par kill-switch chalta hai. Trade-off: thoda recall kam,
probabilities meaningful.

**19. Isotonic calibration ne AUC gira diya tha — kaise fix kiya?**
Isotonic step function ties banata hai, aur ties ranking khatam kar dete hain.
Fix: `TIE_BREAK = 0.02` — calibrated score par ek chhota raw blend add kiya.

**20. Sabse badi weakness kya hai?**
Do: (a) simulated data — production number nahi hai; (b) cold start — bina known
mules ke performance kaafi giregi. Teesri, agar poochhein to: SMURFING par
recall 88.9% hai, sabse kam.

---

*Har number `artifacts/metrics/evaluation.json`, `artifacts/metrics/boi_track.json`,
ya seedhe source code se. Reproduce karne ke liye: `make setup && make all`.*

*Source: https://github.com/archirajpoot/Bodhi_Mule_Hunter*
