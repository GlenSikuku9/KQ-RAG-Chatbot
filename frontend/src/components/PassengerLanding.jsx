import { useMemo, useState } from "react";
import { ArrowRight, BriefcaseBusiness, MessageCircle, Plane, ReceiptText, Search } from "lucide-react";
import "./PassengerLanding.css";
import { KenyaAirwaysLogo } from "./KenyaAirwaysLogo";


const CATEGORIES = [
  "All",
  "Baggage",
  "Check-in",
  "Bookings",
  "Refunds",
  "Payments",
  "Special Assistance",
  "Travel Documents",
];

const FAQS = [
  {
    category: "Check-in",
    question: "How can I check in online?",
    answer: "Use Manage Booking or the Kenya Airways mobile app when online check-in is available for your flight. Keep your booking reference and travel document ready.",
  },
  {
    category: "Baggage",
    question: "What is my baggage allowance?",
    answer: "Your allowance depends on the route, cabin and fare. Check the baggage details shown on your ticket, then ask KQ Support for the policy that applies to your journey.",
  },
  {
    category: "Bookings",
    question: "How can I change my booking?",
    answer: "Eligible bookings can be changed through Manage Booking. Availability, fare conditions and possible price differences apply.",
  },
  {
    category: "Refunds",
    question: "How can I request a ticket refund?",
    answer: "Refund eligibility depends on the ticket conditions and how it was purchased. Use the official refund request channel or contact the original booking agent.",
  },
  {
    category: "Payments",
    question: "What payment methods are available?",
    answer: "Kenya Airways supports common debit and credit cards and selected regional or alternative methods, including M-Pesa and PayPal where available.",
  },
  {
    category: "Special Assistance",
    question: "How do I request special assistance?",
    answer: "Contact Kenya Airways before travel with the assistance you require so the airport and onboard teams can prepare appropriately.",
  },
  {
    category: "Travel Documents",
    question: "What travel documents do I need?",
    answer: "Document requirements depend on your nationality, destination and transit points. Confirm current passport, visa and health requirements before departure.",
  },
];


export function PassengerLanding({ onAskQuestion, onOpenChat }) {
  const [category, setCategory] = useState("All");
  const [search, setSearch] = useState("");
  const [openQuestion, setOpenQuestion] = useState(null);

  const visibleFaqs = useMemo(() => {
    const query = search.trim().toLowerCase();

    return FAQS.filter((faq) => {
      const matchesCategory = category === "All" || faq.category === category;
      const matchesSearch = !query || `${faq.question} ${faq.answer}`.toLowerCase().includes(query);
      return matchesCategory && matchesSearch;
    });
  }, [category, search]);

  function submitSearch(event) {
    event.preventDefault();
    const question = search.trim();

    if (question) {
      onAskQuestion(question);
    }
  }

  return (
    <main className="passenger-landing" id="support-home">
      <section className="support-hero" aria-labelledby="support-heading">
        <div className="support-hero-content">
          <p className="eyebrow">CUSTOMER SUPPORT</p>
          <h1 id="support-heading">How can we help you today?</h1>
          <p>
            Get answers about your journey in English, Kiswahili, or a natural
            mixture of both languages.
          </p>
          <button className="button primary" type="button" onClick={onOpenChat}>
            <MessageCircle aria-hidden="true" />
            Start a conversation
            <ArrowRight aria-hidden="true" />
          </button>
          <div className="support-hero-topics" aria-label="Popular support areas">
            <span><Plane aria-hidden="true" />Flight changes<br />&amp; bookings</span>
            <span><BriefcaseBusiness aria-hidden="true" />Baggage<br />support</span>
            <span><ReceiptText aria-hidden="true" />Refunds<br />&amp; claims</span>
          </div>
        </div>
        <div className="support-hero-art" aria-hidden="true">
          <KenyaAirwaysLogo className="hero-airline-logo" variant="colour" />
        </div>
      </section>

      <section className="support-discovery" aria-labelledby="faq-heading">
        <form className="support-search" role="search" onSubmit={submitSearch}>
          <Search aria-hidden="true" />
          <label className="sr-only" htmlFor="support-search">Search support topics</label>
          <input
            id="support-search"
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search for a topic (e.g. check-in, baggage, refunds)"
          />
          <button className="button primary" type="submit" disabled={!search.trim()}>
            Ask KQ
          </button>
        </form>

        <div className="faq-categories" aria-label="FAQ categories">
          {CATEGORIES.map((item) => (
            <button
              className={item === category ? "faq-filter selected" : "faq-filter"}
              type="button"
              key={item}
              onClick={() => setCategory(item)}
              aria-pressed={item === category}
            >
              {item}
            </button>
          ))}
        </div>

        <div className="faq-heading-row">
          <div>
            <p className="eyebrow">POPULAR SUPPORT TOPICS</p>
            <h2 id="faq-heading">Frequently Asked Questions</h2>
          </div>
          <p>Choose a question or ask KQ Support for details specific to your journey.</p>
        </div>

        <div className="faq-list">
          {visibleFaqs.map((faq) => {
            const isOpen = openQuestion === faq.question;

            return (
              <article className="faq-item" key={faq.question}>
                <button
                  className="faq-question"
                  type="button"
                  onClick={() => setOpenQuestion(isOpen ? null : faq.question)}
                  aria-expanded={isOpen}
                >
                  <span className="faq-icon" aria-hidden="true">?</span>
                  <span>{faq.question}</span>
                  <span className="faq-chevron" aria-hidden="true">⌄</span>
                </button>
                {isOpen && (
                  <div className="faq-answer">
                    <p>{faq.answer}</p>
                    <button className="link-button" type="button" onClick={() => onAskQuestion(faq.question)}>
                      Ask this in KQ Support →
                    </button>
                  </div>
                )}
              </article>
            );
          })}
          {!visibleFaqs.length && (
            <div className="faq-empty">
              <h3>No matching FAQ found</h3>
              <p>Ask KQ Support and we will search the available support information.</p>
              <button className="button secondary" type="button" onClick={submitSearch}>
                Ask KQ Support
              </button>
            </div>
          )}
        </div>
      </section>
    </main>
  );
}
