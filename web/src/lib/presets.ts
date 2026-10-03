// Five example complaints that exercise the main paths through the agent.
// They match the seed data, so with "Reset demo data" + the demo clock they behave the same every time.

export type Preset = {
  title: string;
  expect: string;
  customer_email: string;
  text: string;
};

export const PRESETS: Preset[] = [
  {
    title: "FreshMart demo",
    expect: "Repeat compressor failure under warranty: reserves the part, books TECH-02 at 10:00",
    customer_email: "ops@freshmart.lk",
    text: "Freezer #3 at our Colombo 7 branch stopped cooling again this morning.",
  },
  {
    title: "Big PO: needs approval",
    expect: "Out-of-stock compressor (LKR 121,800): pauses for a manager in the approvals inbox",
    customer_email: "ops@kandycold.lk",
    text: "Freezer #2 at Katugastota stopped cooling. The compressor will not start, it just clicks every few seconds and the cabinet is warming up.",
  },
  {
    title: "Which freezer?",
    expect: "Two units share the name: asks the customer one clarifying question",
    customer_email: "info@peradeniyafresh.lk",
    text: "Freezer #1 is leaking water all over the floor.",
  },
  {
    title: "Vague complaint",
    expect: "Low diagnosis confidence: books an inspection visit, orders nothing",
    customer_email: "ops@freshmart.lk",
    text: "The chiller at Nugegoda sometimes makes an odd noise. Not sure what it is.",
  },
  {
    title: "Unknown sender",
    expect: "Not a customer: hands over to a coordinator without opening a ticket",
    customer_email: "owner@newcornershop.lk",
    text: "Hi, our display freezer stopped working yesterday. Can you send someone?",
  },
];
