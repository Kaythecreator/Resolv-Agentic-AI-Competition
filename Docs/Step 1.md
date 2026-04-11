## Products

Credit Cards

* Credit card  
* Credit card or prepaid card  
* Prepaid card

Loans

* Student loan  
* Vehicle loan or lease  
* Consumer Loan  
* Payday loan  
* Payday loan, title loan, or personal loan  
* Payday loan, title loan, personal loan, or advance loan

Banking

* Checking or savings account  
* Bank account or service

Debt Collection

* Debt collection  
* Debt or credit management

Money Services

* Money transfer, virtual currency, or money service  
* Money transfers  
* Virtual currency

Mortgage

* Mortgage

Other

* Other financial service

## **Agents**

## **1\. Intake / Preprocessing Agent**

Purpose: Clean \+ prepare the data

### What it does:

* Filters out blank narratives  
* Standardizes text (lowercase, remove noise)  
* Uses your Clean Product mapping

## 

## **Classification Agent** 

## Purpose: Understand the complaint

### Outputs:

* Issue type (Fraud, Billing, Service, etc.)  
* Product (you already did this)  
* Keyword-based formulas (SEARCH)

## **Risk / Compliance Agent**

Purpose: Identify dangerous complaints

### Flags:

* Fraud  
* Discrimination  
* Legal threats  
* Regulatory risk

## **Routing Agent**

Purpose: Send complaint to the right team

**Resolution Agent**

Purpose: Define what to do

### Outputs:

* Investigate transaction  
* Reverse charge  
* Correct credit report

Turns classification into action

## Response Generation Agent

Purpose: Communicate with customer

### Output:

* Full response message

Issue Types

## **Credit Reporting Issues**

From your list:

* Incorrect information on credit report  
* Unable to get credit report  
* Credit reporting company investigation  
* Improper use of your report  
* Credit monitoring / identity protection

## **Fraud / Unauthorized Activity**

* Fraud or scam  
* Unauthorized transactions  
* Identity theft  
* Money taken incorrectly  
* Unauthorized withdrawals

## **Billing / Fees / Payments**

* Billing disputes  
* Fees or interest  
* Late fee  
* Charged unexpected fees  
* Payment not credited  
* Trouble during payment process

## **Account Management**

* Managing an account  
* Closing an account  
* Opening an account  
* Account access issues  
* Deposits and withdrawals

## **Loan / Debt Problems**

* Debt collection  
* Attempts to collect debt not owed  
* Loan servicing  
* Struggling to pay loan  
* Mortgage issues  
* Repossession

**Customer Service / Communication**

* Problem with customer service  
* Communication tactics  
* Confusing disclosures  
* Advertising / misleading info  
* Can’t contact lender

## **Intake & Standardization Agent**

Purpose: prepare the complaint for analysis.

Inputs:

* Complaint narrative  
* Raw product  
* Raw issue  
* Company  
* Date  
* Channel or source if you have it

Outputs:

* Clean Product  
* Clean Issue Type  
* Cleaned narrative  
* Missing-data flag

What it does:

* Filters out rows with no narrative  
* Standardizes product labels  
* Standardizes issue labels  
* Removes duplicates or obvious bad rows  
* Makes the complaint ready for downstream agents

## **Complaint Classification Agent**

Purpose: determine what the complaint is mainly about.

Outputs:

* Product  
* Issue Type

What it does:

* Uses your normalized categories  
* Assigns one primary issue category to each complaint  
* Optionally assigns a secondary issue if needed

## Severity Assessment Agent

Purpose: measure how serious the customer harm is.

Outputs:

* Severity \= Low / Medium / High

What it looks for:

* Mentions of fraud  
* Account closure  
* inability to access money  
* repeated unresolved issues  
* large financial harm  
* ruined credit  
* foreclosure / repossession  
* urgent consumer harm

## **Compliance Risk Agent**

Purpose: assess regulatory or legal exposure.

Outputs:

* Compliance Risk \= Low / Medium / High  
* Risk explanation

What it looks for:

* inaccurate credit reporting  
* discrimination  
* unauthorized charges  
* deceptive practices  
* disclosure failures  
* debt collection misconduct  
* legal threats  
* CFPB / regulator / attorney mentions

## **Routing & Escalation Agent**

Purpose: send the complaint to the right internal team.

Outputs:

* Assigned Team  
* Escalation level  
* SLA priority if you want

**Resolution & Response Agent**

Purpose: produce the actual action plan and customer response.

Outputs:

* Resolution Plan  
* Remediation Steps  
* Preventive Recommendation  
* Customer Response Draft

What it does: For each complaint, it generates:

* what team should do  
* what the customer should be told  
* what should be checked internally  
* what process improvement might prevent recurrence

Severities

### **High severity**

* Fraud / scam  
* Unauthorized transactions  
* Identity theft  
* Incorrect information on credit report  
* Attempts to collect debt not owed  
* Took or threatened legal action  
* Loan modification / foreclosure  
* Struggling to pay mortgage  
* Repossession  
* Money was taken from bank account incorrectly  
* Account funds unavailable  
* Severe payment processing failures

### **Medium severity**

* Billing disputes  
* Fees or interest problems  
* Trouble during payment process  
* Managing an account  
* Closing an account  
* Opening an account  
* Trouble using card  
* Problem with customer service  
* Problem getting a card  
* Dealing with lender or servicer  
* Confusing disclosures  
* Payment not credited  
* Loan servicing problems

### **Low severity**

* Advertising / marketing complaints  
* Rewards issues  
* Minor customer service frustration  
* General communication issues  
* Other service problem  
* Convenience checks  
* Minor disclosure issues without direct harm  
* Small feature complaints

### Compliances

### **High compliance risk**

* Incorrect information on credit report  
* Improper use of your report  
* Problem with a company's investigation into an existing issue  
* Problem with credit reporting investigation  
* Attempts to collect debt not owed  
* Took or threatened legal action  
* False statements or representation  
* Communication tactics in debt collection  
* Fraud or scam  
* Unauthorized transactions  
* Identity theft  
* Confusing or misleading advertising  
* Disclosure failures  
* Threatened to contact others improperly  
* Improper contact or sharing of info  
* Unauthorized withdrawals or charges

### **Medium compliance risk**

* Billing disputes  
* Trouble during payment process  
* Problem with lender charging account  
* Payment not credited  
* Loan servicing issues  
* Application processing delay  
* Dealing with lender or servicer  
* Problem with customer service  
* Closing account issues  
* Funds not available when promised  
* Account terms and changes

### **Low compliance risk**

* Rewards  
* Minor service issues  
* General account management inconvenience  
* Convenience checks  
* Other feature complaints  
* Minor marketing annoyance without deceptive conduct  
* Routine frustration without rights or disclosure concerns

\[1\] **Raw Complaint Data** (CFPB CSV)  
↓  
\[2\] **Intake & Standardization Agent** → Clean and structure the data  
↓  
\[3\] **Classification Agent**→ Understand what the complaint is about  
↓  
\[4\] **Severity Agent \+ Compliance Risk Agent** → Evaluate importance and danger  
↓  
\[5\] **Routing & Escalation Agent** → Decide where the complaint goes  
↓  
\[6\] **Resolution Agent**→ Decide what to do  
↓  
\[7\] **Response Agent** → Communicate with the customer  
↓  
\[8\] **Insights / Metrics Layer →** Learn from complaints

## **Accuracy**

* Classification Accuracy / F1 Score  
* Severity Accuracy  
* High-Risk Precision & Recall

## **Resolution Quality**

* Resolution Appropriateness  
* Escalation Accuracy  
* Resolution Completeness  
* Root Cause Accuracy

## **Fairness**

* Cross-Product Consistency  
* Outcome Parity  
* Error Distribution  
* Stability Over Time

## **Customer Impact**

* First-Touch Resolution Rate  
* Time-to-Resolution Reduction  
* Reopen Rate  
* Response Quality Score  
* Customer Effort (proxy)


Ground Truth Label Mapping

## **1\. Ground Truth Source →** The ground truth labels are derived from the CFPB dataset’s original Issue field.

## **2\. Standardized Issue Categories →** We map the original CFPB issue labels into the following categories:

* Credit Reporting Issues  
* Fraud / Unauthorized Activity  
* Billing / Fees / Payments  
* Account Management  
* Loan / Debt Problems  
* Customer Service / Communication  
* Other

## **3\. Label Mapping** 

### **Credit Reporting Issues**

* Incorrect information on credit report  
* Unable to get credit report or score  
* Improper use of credit report  
* Credit reporting investigation issues  
* Credit monitoring / identity protection

### **Fraud / Unauthorized Activity**

* Fraud or scam  
* Unauthorized transactions  
* Identity theft  
* Unauthorized withdrawals or charges  
* Money taken incorrectly

### **Billing / Fees / Payments**

* Billing disputes  
* Fees or interest  
* Late fee  
* Charged unexpected fees  
* Payment not credited  
* Trouble during payment process

### **Account Management**

* Managing an account  
* Opening an account  
* Closing an account  
* Deposits and withdrawals  
* Trouble accessing account

### **Loan / Debt Problems**

* Debt collection  
* Attempts to collect debt not owed  
* Loan servicing issues  
* Struggling to pay loan or mortgage  
* Repossession  
* Foreclosure

### **Customer Service / Communication**

* Problem with customer service  
* Communication tactics  
* Confusing or misleading disclosures  
* Advertising or marketing issues  
* Cannot contact lender or company

