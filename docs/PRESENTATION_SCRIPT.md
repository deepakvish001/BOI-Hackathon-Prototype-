# BODHI MULE HUNTER AI — 30 Minute Presentation Script

**Format:** Hinglish, bola jaane ke liye likha gaya — padha jaane ke liye nahi.
**Pace:** ~145 words/minute. Timestamps guide hain, jail nahi.
**Style note:** Har section ek sawaal se shuru hota hai aur ek jawaab par khatam.
Numbers dheere boliye — wahi yaad rehte hain.

> Har number is script mein `artifacts/metrics/evaluation.json` se aaya hai.
> Agar aap model dobara train karein aur numbers badlein, to script bhi update
> kijiye — warna aap wo daawa kar rahe honge jo code nahi karta.

---

## [00:00 – 02:30] Part 1 — Ek phone call se shuruaat

*(Slide: blank / ek phone ki image. Dheere shuru kariye.)*

Main aaj shuruaat kisi architecture diagram se nahi karunga. Ek phone call se
karunga.

Sochiye — Indore ki ek retired school teacher hain. Shaam ke saat baje unke
phone par ek call aata hai. Doosri taraf se koi kehta hai, "Madam, main bank se
bol raha hoon, aapka KYC expire ho gaya hai." Woh ghabra jaati hain. Ek link
aata hai, ek app install hoti hai, ek OTP share ho jaata hai.

Aur agle **teen minute** mein unke khaate se aath lakh rupaye nikal jaate hain.

Ab yahan par ek sawaal poochhiye apne aap se — **wo paisa gaya kahan?**

Jawaab ye hai ki wo paisa ek jagah nahi gaya. Wo aath lakh, teen minute ke andar,
**sattaais alag-alag khaaton** mein toot gaya. Har khaate mein tees hazaar,
pachchees hazaar. Phir wahan se aage doosre khaaton mein. Phir teesre. Aur
chauthe step par — kisi chhote sheher ke ATM se, ya kisi AePS micro-ATM se — wo
cash ban gaya.

Aur cash ban jaane ke baad? Cash ka koi audit trail nahi hota. Wo paisa
technically, permanently, gaya.

Ab jab wo teacher agle din 1930 par call karti hain — National Cybercrime
Helpline — tab tak **chhattis ghante** ho chuke hote hain. Complaint register
hoti hai, ticket banta hai, bank ko jaata hai. Aur bank jab us pehle khaate ko
freeze karta hai, us khaate mein bacha hota hai — **zero**.

*(Pause. Audience ko dekh kar.)*

Ye jo sattaais khaate the na — inhe kehte hain **mule accounts**. Aur aaj ke
tees minute mein hum sirf ek sawaal ka jawaab dhoondhenge: **kya un khaaton ko,
paisa pahunchne se pehle ya pahunchte hi, pakda ja sakta hai?**

---

## [02:30 – 05:00] Part 2 — Mule account hota kya hai

*(Slide: "A mule account is a normal account")*

Sabse pehle ye samajhna zaroori hai ki mule account **fake account nahi hota**.
Ye sabse badi galatfehmi hai.

Mule account ek **bilkul asli khaata** hota hai. Uska KYC genuine hai. Aadhaar
asli hai. PAN asli hai. Jis insaan ke naam par hai, wo insaan asli hai aur zinda
hai. Video KYC hui hai. Sab kuch legal hai.

Farak sirf itna hai ki us khaate ka **control** kisi aur ke paas hai.

Ye teen tareeke se hota hai. Pehla — **rent**. Kisi student ko, kisi daily wage
worker ko paanch hazaar rupaye mahina milta hai sirf apna khaata "use karne
dene" ke liye. Doosra — **dhokha**. "Work from home job hai, aapke account mein
paisa aayega, aap withdraw karke humein de dena, aapko commission milega." Aur
teesra — **malicious app**. Wo trojan APK jo victim ke phone par install hui
thi, uske andar beneficiary account number **hardcoded** hota hai.

Ab yahan par asli technical problem shuru hoti hai. *(Ruk kar.)*

Agar aap us mule account ki taraf akele dekhein — sirf us khaate ko, apne aap
mein — to usme **kuch bhi galat nahi dikhta**. Paise aaye. Paise gaye. Balance
zero. Har transaction individually legal hai. Har transaction limit ke andar
hai. Koi rule technically toota hi nahi.

Jo cheez galat hai wo do jagah chhupi hai:

Ek — paise ke aane-jaane ka **shape**. Chalees credits nabbe minute mein, aur
raat ke teen bajkar baarah minute par pura balance khaali.

Do — us khaate ki **sangat**. Uske counterparties kaun hain. Wo kis device se
login karta hai. Us device se aur kaun-kaun login karta hai.

**Shape aur sangat.** Ye do cheezein yaad rakhiye — pura project inhi do shabdon
par khada hai.

---

## [05:00 – 08:30] Part 3 — Aaj ka system kyun fail karta hai

*(Slide: rule engine ka table)*

Ab sawaal ye hai — bank ke paas to already transaction monitoring system hai.
Wo kaam kyun nahi karta?

Iska jawaab dene ke liye humne guess nahi kiya. Humne **wo system banaya**.
Aath rules ka ek engine, bilkul waise jaise banks aaj deploy karte hain. Jaise:
"chobees ghante mein paanch se zyada payers se paanch lakh se upar credit" —
alert. "Nabbe din se naya khaata jiska turnover do lakh se upar" — alert.
"Structuring pattern" — alert.

Ye rules bewakoof nahi hain. Ye samajhdaar logon ne banaye hain.

Ab suniye kya hua jab humne isse **baarah hazaar khaaton** aur **aath lakh
chauntees hazaar transactions** par chalaya.

Us population mein asli mule the — **ek sau ikyavan**. Yaani **1.26 percent**.

Rule engine ne alerts raise kiye — **nau hazaar chhe sau sattaalis**.

*(Pause. Number ko sink hone dijiye.)*

Nau hazaar. Baarah hazaar khaaton mein se. Yaani har paanch mein se chaar khaate
par shak.

Aur un nau hazaar mein se sahi kitne the? **Ek sau atthaais.**

Precision — **1.33 percent**.

Iska matlab hindi mein samjhiye: ek analyst subah baithta hai, sau case kholta
hai, aur unme se **ninyanve** case bilkul innocent log hote hain. Ek. Sirf ek
sahi nikalta hai.

Ab koi bhi insaan — chahe wo kitna hi dedicated ho — is queue ko seriously nahi
le sakta. Kyunki uska dimaag seekh jaata hai ki "ye queue jhooth bolti hai."
Isko **alert fatigue** kehte hain, aur ye is poori industry ki sabse badi
problem hai.

Aur dhyaan dijiye — problem ye nahi hai ki rules kamzor hain. Problem ye hai ki
rule ke paas **vocabulary hi nahi hai**.

Ek rule ye keh hi nahi sakta: "is khaate ke counterparties khud suspicious
hain." Rule ek khaate ko akele dekhta hai. Wo network dekh hi nahi sakta.

Aur jab aapke paas signal express karne ki bhasha na ho, to aap ek hi cheez kar
sakte hain — **sensitivity badha do**. Threshold girao. Aur zyada alert nikalo.
Aur wahi nau hazaar ka number ban jaata hai.

---

## [08:30 – 11:00] Part 4 — Humara core insight

*(Slide: "Three views")*

To humne kya kiya?

Humne ye nahi socha ki "ek behtar model bana lete hain." Humne ye socha ki
**problem ko ek se zyada aankhon se dekhna padega.**

Ek mule account teen alag-alag tareekon se ajeeb hota hai, aur teenon
ajeebpan ko dekhne ke liye teen alag mathematics chahiye:

**Pehli aankh — behaviour.** Us khaate ka apna barta'v. Kitni velocity hai,
kitna fan-in hai, kitne din dormant tha, kitni jaldi paisa nikla. Ye ek
**tabular** problem hai, aur iske liye gradient boosting — XGBoost — sabse
achha tool hai.

**Doosri aankh — network.** Ye khaata kis device se chalta hai, us device se aur
kaun chalta hai, kiske saath paisa exchange karta hai. Ye ek **graph** problem
hai. Aur iske liye humne **GraphSAGE** use kiya — ek inductive graph neural
network, jo naye node par bhi kaam karta hai bina dobara train kiye. Ye zaroori
hai kyunki bank mein har roz naye khaate khulte hain.

**Teesri aankh — time.** Events ka **order**. Chalees credits aana ek baat hai;
chalees credits aana aur phir turant nikal jaana — bilkul doosri baat. Ye ek
**sequence** problem hai, aur iske liye humne Temporal Graph Network banaya —
har khaate ki ek memory jo har event par update hoti hai.

Ab yahan ek baat main zor dekar kehna chahta hoon, kyunki ye is project ka sabse
important design decision hai:

**Ye teen models ek doosre ka data nahi dekhte.**

XGBoost ko graph ka pata nahi. Graph model ko sequence ka pata nahi. Ye
deliberate hai. Kyunki agar teenon ek hi cheez dekhenge, to teenon ek hi galti
karenge — aur ensemble ka koi fayda nahi hoga. Alag evidence par khade models
hi ek doosre ki galti pakad sakte hain.

Aur iske upar humne ek chauthi cheez rakhi — **intelligence**. NCRP tickets,
1930 ki complaints, CERT-In ke IOCs, aur wo malicious APK se nikale hue account
numbers.

---

## [11:00 – 14:00] Part 5 — Data, aur wo hissa jise log chhupate hain

*(Slide: hard negatives table. Ye section dheere boliye — ye credibility ka
section hai.)*

Ab main aapko wo cheez batata hoon jo is presentation ka sabse imaandaar hissa
hai.

Humne data **simulate** kiya. Aur mujhe pata hai ki abhi kuch logon ke dimaag
mein aaya — "arey, synthetic data par to koi bhi model achha score kar dega."

**Bilkul sahi soch hai.** Aur isiliye main aapko batata hoon ki humne uska kya
kiya.

Pehle ye samjhiye ki simulate kyun kiya. Account-level mule labels, device
linkage ke saath, IP linkage ke saath — ye data commercially sensitive hai,
personally identifying hai, aur legally restricted hai. Koi bank ise export nahi
kar sakta. Aur koi public dataset mein ye exist nahi karta. To simulation ka
alternative "real data" nahi tha — alternative **koi evaluation hi nahi** tha.

Ab asli baat. Naive synthetic data par har model 0.99 AUC deta hai, kyunki
usme mule bilkul alag dikhte hain. To humne apne simulated bank mein
**jaanbujhkar wo legitimate khaate daale jo mule jaise hi dikhte hain.**

Suniye:

**Business Correspondent agents** — Bank Mitra. Teen sau do khaate. Ek hi
handheld device se din bhar mein bees-tees logon ka AePS cash transaction. Ab
socho — ek device, bahut saare accounts, saara din cash withdrawal. Ye **exactly**
wahi fingerprint hai jo ek device farm ka hota hai. Ye log bilkul legal hain,
government scheme ke under kaam karte hain.

**Community collectors** — ek sau saat khaate. Chit fund ke organiser, tuition
batch ke collector, festival committee ke treasurer. Ajnabiyon se burst mein
paisa aata hai, aur nabbe percent kuch ghanton mein aage chala jaata hai. Ye
**exactly** collection mule ka pattern hai.

**Chhote businesses** — ek sau atthasath khaate, rozana bahut saare alag logon
se P2P payments.

**Genuine dormant wake** — ek sau atthasath khaate. Student ka khaata jo chhe
mahine soya tha, aur ab semester fees ke liye achanak active ho gaya. Ye
**exactly** dormant-burst typology jaisa lagta hai.

Ye chaaron populations humne **isliye** daale taaki hamara model unpar phans
jaaye.

Aur result? *(Slide par numbers.)*

BC agents par false positive rate — **0.33 percent**. Teen sau do mein se ek.
Community collectors — **2.80 percent**. Chhote business — **0 percent**.
Dormant wake — **3.57 percent**. Aur aam retail customers, das hazaar paanch sau
chaar khaate — **0.77 percent**.

*(Pause.)*

**Yahi wo number hai jo hamare precision claim ko meaning deta hai.** Kyunki
agar hamara model in populations par phans jaata, to hamara 99 percent precision
ek jhooth hota — bas is baat ka jhooth ki humne aasan data chuna tha.

---

## [14:00 – 19:00] Part 6 — Architecture: nau layers

*(Slide: 9-layer diagram)*

Ab aate hain system par. **Nau layers**, har layer ka ek named agent.

**Layer 1 — Transaction Monitoring Agent.** Ye ingestion hai. UPI, IMPS, NEFT,
RTGS, AePS, ATM, card, wallet — aath alag rails. Har rail ka apna format. Ye
layer sabko **ek schema** mein laati hai. Aur sirf transactions nahi —
government ke cyber fraud tickets bhi isi schema mein aate hain.

**Layer 2 — Feature Engineering Agent.** Yahan **ikhattar** behavioural
features bante hain. Velocity, fan-in, fan-out, dormancy, burst, structuring proximity,
device reuse. Ek technical baat jo main highlight karna chahta hoon: ye saare
features **point-in-time** hain. Yaani jab hum kisi din ke liye feature banate
hain, to sirf us din tak ka data use hota hai. Agar aap galti se future ka data
use kar lein — jise **leakage** kehte hain — to aapka model lab mein shandaar
lagega aur production mein bekaar nikal jaayega.

**Layer 3 — Graph Builder Agent.** Yahan account, device aur IP ka knowledge
graph banta hai. CSR format mein, incrementally.

Ab teen model layers:

**Layer 4 — XGBoost.** Ye **cheap screening** hai. Ye aasan majority ko sasta
mein nipta deta hai. Iska akela ROC-AUC — **0.9946**.

**Layer 5 — GraphSAGE.** Ye graph par chalta hai. Har account apne padosiyon se
message leta hai, mean aggregator se, do hops tak. Iska akela ROC-AUC —
**0.9962**. Ye XGBoost se **thoda behtar** hai, akele.

Aur ek important implementation detail — humne PyTorch use **nahi** kiya.
GraphSAGE aur TGN dono **pure NumPy** mein likhe hain, gradients haath se derive
karke. Aur wo gradients humne test kiye hain — `tests/test_gradients.py` mein,
central finite differences ke against, 2e-5 tolerance par. Iska practical fayda
ye hai ki poora engine ek laptop CPU par chalta hai, bina GPU, bina CUDA, bina
kisi deep learning framework ke. Ek bank ke liye ye deployment ka farak hai.

**Layer 6 — Temporal Graph Network.** Har account ki ek memory vector hai, jo ek
GRU se har event par update hoti hai. Time ko encode karne ke liye ek learnable
time2vec encoding hai. Iska akela ROC-AUC — **0.9628**.

**Layer 7 — Fusion.** Ispar main alag se baat karunga, ek minute mein.

**Layer 8 — Explainability.** TreeSHAP aur GNNExplainer.

**Layer 9 — Alert Generation aur Kill-switch.**

---

## [19:00 – 21:00] Part 7 — Fusion, aur do cheezein jo negotiable nahi thi

*(Slide: fusion)*

Fusion sunne mein simple lagta hai — teen score ka average le lo. Lekin yahan do
constraints the jinpar hum samjhauta nahi kar sakte the.

**Pehla — monotonicity.** Agar koi bhi layer ka score badhta hai, to final risk
score **kabhi ghat nahi sakta**. Ye mathematically enforce kiya hai —
non-negative weights, log-odds space mein, constrained optimisation se. Kyunki
socho agar aisa na ho: aap ek investigator ko kaise samjhaoge ki "graph model ne
zyada shak jataya, isliye final risk kam ho gaya"? Wo system par kabhi bharosa
nahi karega.

**Doosra — calibration.** Score sirf ranking nahi de sakta. Agar system kehta
hai **80 percent probability**, to jitne khaaton ko 80 kaha hai, unme se
**lagbhag 80 percent** actually mule hone chahiye. Warna aap threshold set nahi
kar sakte, aur risk-based capital allocation nahi kar sakte.

Humne isko naapa hai. **Expected Calibration Error — 0.0047.** Yaani aadha
percent se bhi kam ka gap.

Aur ek imaandaar baat fusion ke baare mein. Jab humne weights seekhe, to TGN ko
mila weight — **0.009**. Lagbhag zero.

Iska matlab kya? Iska matlab ye ki TGN jo dekh raha tha, wo zyaadatar XGBoost
aur GraphSAGE pehle se hi capture kar rahe the. Wo **redundant** tha, is dataset
par.

Hum isko chhupa sakte the. Hum nahi chhupa rahe — kyunki fusion ka poora point
hi yahi hai ki wo **naap kar** decide kare, humaari umeed par nahi chale.

---

## [21:00 – 24:30] Part 8 — Results

*(Slide: results table. Ye section confidently boliye.)*

Ab numbers.

Fused model ka **ROC-AUC 0.9966**, aur **PR-AUC 0.9707**. Aur dhyaan dijiye —
fused model teenon individual models se behtar hai. Yahi complementarity ka
proof hai, naapa hua.

Lekin AUC ek academic number hai. Asli sawaal ye hai: **rule engine ke against
kya hua?**

Aur yahan hum ek honest comparison karte hain — **same recall par**. Kyunki
agar aap recall match nahi karte, to koi bhi system keh sakta hai "maine zyada
pakda" — bas zyada alert nikaal kar.

To, same recall — **84.77 percent** — par:

| | Rule engine | BODHI |
|---|---|---|
| Alerts | 9,647 | **129** |
| True positives | 128 | 128 |
| False positives | 9,519 | **1** |
| Precision | 1.33% | **99.22%** |

*(Pause.)*

Nau hazaar chhe sau sattaalis case se ek sau untees case. **Wahi** true
positives. False positives nau hazaar paanch sau unnees se **ek**.

**99.989 percent false positives khatam. 75 guna precision uplift.**

Ab teen aur results jo mere hisaab se sabse important hain:

**Ek — Out of time test.** Humne teen aise rings rakhe jo simulation ke
**aakhri hisse** mein active hue — training ke baad. Yaani model ne unhe kabhi
dekha hi nahi tha, kisi bhi roop mein. Un rings mein chhabbis mule the.

Model ne pakde — **chhabbis**. **100 percent recall.** Median score — 100 mein
se 100.

**Do — Independence from tickets.** Ye sabse important slide hai. Kul ek sau
ikyavan mule the. Unme se sirf **tirpan** ka naam kisi NCRP ticket mein tha.
**Athanave** mule aise the jinke baare mein kisi ne kabhi complaint hi nahi ki.

Hamare model ne pakde ek sau saintaalis. Aur unme se **chauranve** aise the
jinka **kisi ticket mein naam nahi tha**.

Yaani hamare 64 percent detections **complaint aane ka intezaar nahi kar rahe
the**. Un never-reported mules par recall — **95.92 percent**.

Ye is project ka sabse bada strategic point hai. Kyunki agar aapka system sirf
tickets ko follow karta hai, to aap hamesha **chhattis ghante peeche** hain. Aur
chhattis ghante mein paisa cash ban chuka hota hai.

**Teen — Latency.** Median inline decision — **0.054 milliseconds**. p99 —
**0.113 milliseconds**. Throughput lagbhag **solah hazaar six sau** decisions per
second, ek core par.

Ye isliye possible hai kyunki humne architecture ko **do hisson mein toda**:
graph aur temporal layers schedule par chalte hain aur ek standing risk cache
karte hain; authorization path sirf us cache ko aur transaction ke apne
attributes ko dekhta hai, graph **traverse nahi karta**. UPI ki latency budget
mein ye hi ek tareeka hai.

*(Optional, agar time hai:)* Aur hum ye bhi batate hain ki **kahan kamzor
hain** — smurfing par recall 88.9 percent, cash-out role par 92.1 percent.
Average sab chhupa deta hai; isliye humne har typology ka number alag diya hai.

---

## [24:30 – 27:00] Part 9 — Explanation, aur "refuse karne wala" kill-switch

*(Slide: explainability screenshot)*

Ab ek sawaal — model ne 87 score diya. **Kyun?**

Agar iska jawaab nahi hai, to ye system ek bank mein deploy nahi ho sakta.
Kyunki RBI ke saamne "model ne bola" defence nahi hai.

Humne do alag explanation lagaye hain, kyunki ek se kaam nahi chalta:

**TreeSHAP** — exact Shapley values, approximate nahi. Iski property ye hai ki
saare attributions **jodkar theek model ke margin ke barabar** aate hain. Ye
batata hai ki *kaunse features* ne score banaya.

Lekin SHAP ek sawaal ka jawaab structurally de hi nahi sakta — **kaunse
rishton ne**. Uske liye **GNNExplainer** hai, jo graph ke edges par ek mask
seekhta hai aur batata hai ki kaunsi edges hataane par score girta hai.

Aur donon ka output ek AML officer ki bhasha mein likha jaata hai, aur seedhe
**STR — Suspicious Transaction Report** ke "grounds of suspicion" section mein
chala jaata hai. Yaani jo evidence investigator ne screen par dekha, aur jo
FIU-IND ko file hua — wo **same** hai, alag nahi ho sakta.

Ek compliance detail jo main mention karna chahunga: CTR — Cash Transaction
Report — hum **per account per calendar day** aggregate karte hain. Per
transaction nahi. Kyunki obligation us level par lagti hai — aur **theek isiliye**
structuring per-transaction check ko haraa deti hai.

*(Slide: kill-switch)*

Ab sabse sensitive hissa — **account freeze karna**.

Ek galat freeze ka matlab hai ek insaan jo apni dawai ka payment nahi kar
paa raha. Koi bhi AUC improvement isko justify nahi karta.

Isliye hamara kill-switch ek threshold nahi hai — wo **constraints ka set** hai:

- Critical band se neeche automation freeze kar hi **nahi sakta**
- Full freeze ke liye **kam se kam do independent layers** ka agree karna
  zaroori hai
- Har action ka ek **time-to-live** hai, aur har action **reversible** hai
- Per hour automated freezes **rate-limited** hain — taaki agar model degrade ho
  ya koi feed poison ho jaaye, to blast radius bounded rahe
- Aur **salary, pension aur benefit accounts** automated freezing se poori tarah
  **bahar** hain

Aur jab koi constraint fire karta hai, to action **downgrade** hota hai, escalate
nahi — aur human review ke liye flag ho jaata hai.

Har decision, **har refusal included**, ek hash-chained append-only audit log
mein likha jaata hai.

Main ye line zor dekar kehna chahta hoon: **ye system 'na' keh sakta hai.** Aur
mere hisaab se ek AI system ki sabse important capability yahi hai.

---

## [27:00 – 28:30] Part 10 — Organisers ke apne dataset par

*(Slide: BOI track)*

Ab ek alag hissa. Organisers ne humein apne Phase-2 dataset ka column
dictionary diya, aur kaha ki evaluation ek aise validation file par hogi jo
share nahi ki gayi.

Wo dataset alag cheez hai — usme ek row ek **alert** hai, ek account nahi.
Yaani population pehle se bank ke rules se filter ho chuki hai, aur kaam
detection nahi, **triage** hai. To humne ek doosri pipeline banayi, seedhe unke
schema par — **3,924 columns**.

Aur wahan humein do cheezein mili jo main share karna chahta hoon.

**Pehli — chaar columns mein jawaab chhupa hua hai.**

`FRAUD_SUSPECTED`, `FALSE_POSITIVE`, `OTHER_RESOLUTION`, `UNATTENDED` — dictionary
inhe "resolution status flag" kehti hai. Yaani analyst ne alert ko **kaise band
kiya**. Ab socho — ek **khula** alert, jise score karna hai, usme ye hote hi
nahi.

Humne naapa ki inhe include karne se kya milta hai: PR-AUC **0.177 se 0.972**.
Saadhe paanch guna. Akela `FRAUD_SUSPECTED` poore gain ka ek tihai.

Ye model nahi hai. Ye doosre column mein likha hua jawaab dekh lena hai. Humne
inhe **default quarantine** kar diya hai.

**Doosri — bank ke apne atthaarah features jeet gaye.**

Dictionary mein 3,923 mein se **18 predictors** "bank finalized" mark hain.
Humne usko instruction nahi, **hypothesis** maana aur test kiya. Chaar
strategies, repeated stratified cross-validation:

Bank ke 18 features — **0.712 ROC-AUC**. Saare 5,926 columns — **0.618**.
Automatic selection — 0.622.

**Domain knowledge ne brute force ko haraya.** Aur ye theory bhi predict karti
hai — jab positives kuch sau hain aur predictors hazaaron, to zyada features
matlab zyada noise.

Aur ek zaroori disclosure: **ye numbers organisers ke asli data par nahi hain** —
wo data release hi nahi hua tha. Ye ek stand-in table par hain jo unke exact
3,924 columns ke saath generate hoti hai. Ye batate hain ki pipeline chalti hai
aur khud ko dhokha nahi deti — ye model performance **nahi** hain, aur hum inhe
waisa present nahi karenge.

---

## [28:30 – 30:00] Part 11 — Limitations, aur closing

*(Slide: limitations. Ye section jaldi mat boliye — ye aapki credibility hai.)*

Ab wo cheezein jo hum **nahi** kar sakte. Main ye khud bata raha hoon, kyunki
jo prototype apni limitations chhupata hai wo deployment team ke kisi kaam ka
nahi hai.

**Ek** — results simulated data par hain. Ye architecture ko prove karte hain,
production performance ko nahi.

**Do** — SHIELD component sirf **static** APK analysis karta hai. Dynamic
sandbox ke liye instrumented Android image chahiye, jo self-contained ship nahi
ho sakta.

**Teen** — graph layers real-time nahi hain. Poori population ka re-score
lagbhag chalees second leta hai. To jis khaate ka neighbourhood pichhle batch ke
baad badla hai, wo thodi purani structure par score ho raha hai.

**Chaar** — system **ek institution** dekhta hai. Jo rings kai banks se hokar
jaate hain, wo aadhe hi dikhte hain. Ye data-sharing ki problem hai, modelling
ki nahi.

**Paanch** — sabse strong single feature hai neighbourhood risk, jo confirmed
cases se propagate hota hai. Iska matlab ek **cold start** — jahan koi known
mule nahi hai — wahan performance kaafi giregi.

**Aur chhe** — aur ye mere liye sabse important hai — **disparate impact abhi
evaluate nahi hua hai.** Minimum-KYC status aur shared-device features
predictive hain — lekin ye kam income wale aur joint-family households se
correlate karte hain. Koi bhi asli deployment se pehle **alert-rate parity**
naapna zaroori hai. Hum ye naap nahi paaye, aur isliye keh rahe hain.

*(Slide: closing)*

To khatam karte hain wahan se jahan shuru kiya tha.

Mule detection aaj isliye fail nahi hota ki signal maujood nahi hai. Wo isliye
fail hota hai ki **single-account rule engine us signal ko express hi nahi kar
sakte.**

Problem ko teen complementary aankhein do — behaviour, network, aur time —
unhe ek monotonicity constraint ke andar ek calibrated score mein jodo — aur
false positives **99.98 percent** gir jaate hain, wahi recall par. Explanations
itne specific hote hain ki file kiye ja sakein. Aur containment itna cautious
hota hai ki automate kiya ja sake.

Ye poora system — engine, simulator, dashboard, aur wo har script jisse ye
saare numbers dobara paida kiye ja sakte hain — **ek sau ikkees tests** ke saath
open source release ho chuka hai.

Ek aakhri baat. *(Pause.)*

Hum ye daawa nahi kar rahe ki humne fraud solve kar diya hai. Hum ye keh rahe
hain ki us Indore ki teacher ke aath lakh rupaye — jo sattaais khaaton mein
toote the — un khaaton ka **shape aur unki sangat**, complaint aane se pehle,
machine ko dikh sakti thi.

Aur agar wo dikh sakti hai, to usse rok bhi sakte hain.

Dhanyavaad.

---

## Delivery notes

**Sabse important numbers — inhe zabaani yaad rakhiye:**

| Number | Kya hai |
|---|---|
| 9,647 → 129 | rule engine ke alerts vs BODHI, same recall par |
| 1.33% → 99.22% | precision |
| 99.989% | false positives removed |
| 0.9966 / 0.9707 | fused ROC-AUC / PR-AUC |
| 0.054 ms | median inline decision |
| 64% | detections jo kisi ticket mein the hi nahi |
| 100% | out-of-time rings par recall |
| 0.33% | BC agents par false positive rate |
| 0.177 → 0.972 | leakage columns ka asar (BOI track) |

**Timing ke liye compression order** — agar time kam pad raha ho, is order mein
kaato:
1. Part 10 ka BOI hissa chhota kariye (do findings ki jagah sirf leakage)
2. Part 6 mein layer-by-layer ki jagah "teen views" par hi rahiye
3. Part 8 ka typology paragraph chhod dijiye

**Kabhi mat kaatiye:** Part 5 (hard negatives) aur Part 11 (limitations). Wahi
do sections aapko baaki teams se alag karte hain — sab results dikhate hain,
bahut kam log ye dikhate hain ki unhone apne result ko **todne ki koshish** ki.

**Q&A ke liye tayyar rehne wale teen sawaal:**

*"Synthetic data par ye numbers kya matlab rakhte hain?"* → Part 5 dohraiye: hard
negatives ke FP rates. Kahiye ki architecture prove hua hai, production number
nahi — aur ye report mein likha hua hai.

*"Real bank mein deploy kaise hoga?"* → `docs/DEPLOYMENT.md`. 2 GB RAM, ek
container, 23 second boot, koi GPU nahi. Batch graph scoring + inline cached
decision ka split.

*"Agar model galat freeze kar de to?"* → Kill-switch constraints. Do independent
layers ka agreement, TTL, reversible, rate-limited, salary/pension accounts
exempt, aur har refusal audit log mein.
