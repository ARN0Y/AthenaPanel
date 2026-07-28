// Package hooks is the local endpoint pppd's ip-up / ip-down scripts call.
//
// On the master those scripts talk to the panel directly. On a node there is no
// panel to talk to, and making them reach across the internet on every connect
// would put a WAN round trip in the authentication path — a slow link would
// then delay or fail customer logins, which is exactly backwards.
//
// So they talk to this instead: loopback only, no auth, because anything able
// to reach 127.0.0.1 on this machine already has root-equivalent access to the
// VPN stack. The agent records the session locally and the hub learns about it
// in the next report, asynchronously, off the critical path.
package hooks

import (
	"encoding/json"
	"log"
	"net"
	"net/http"
	"time"
)

// Event is one session transition reported by a ppp script.
type Event struct {
	Username string `json:"username"`
	Ifname   string `json:"ifname"`
	PeerIP   string `json:"peer_ip"`
	Pid      int32  `json:"pid"`
	// Only on ip-down: pppd's own totals for the finished session.
	InOctets    uint64 `json:"in_octets"`
	OutOctets   uint64 `json:"out_octets"`
	SessionTime int64  `json:"session_time"`
}

// Handler receives session transitions. Returning false from OnUp tells the
// script to drop the link.
type Handler interface {
	OnUp(Event) (allowed bool, reason string)
	OnDown(Event)
}

type Server struct {
	h    Handler
	srv  *http.Server
	addr string
}

func New(addr string, h Handler) *Server {
	return &Server{h: h, addr: addr}
}

func (s *Server) Start() error {
	mux := http.NewServeMux()
	mux.HandleFunc("/session-up", s.up)
	mux.HandleFunc("/session-down", s.down)
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})

	ln, err := net.Listen("tcp", s.addr)
	if err != nil {
		return err
	}
	s.srv = &http.Server{
		Handler: mux,
		// A ppp script must never hang: pppd is waiting on it, and a stuck hook
		// stalls the connection it is trying to report.
		ReadTimeout:  3 * time.Second,
		WriteTimeout: 3 * time.Second,
	}
	go func() {
		if err := s.srv.Serve(ln); err != nil && err != http.ErrServerClosed {
			log.Printf("hook server stopped: %v", err)
		}
	}()
	log.Printf("hook endpoint listening on %s", s.addr)
	return nil
}

func (s *Server) Close() {
	if s.srv != nil {
		_ = s.srv.Close()
	}
}

func decode(w http.ResponseWriter, r *http.Request) (Event, bool) {
	var ev Event
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return ev, false
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 8<<10)).Decode(&ev); err != nil {
		http.Error(w, "bad json", http.StatusBadRequest)
		return ev, false
	}
	if ev.Username == "" || ev.Ifname == "" {
		http.Error(w, "username and ifname are required", http.StatusBadRequest)
		return ev, false
	}
	return ev, true
}

func (s *Server) up(w http.ResponseWriter, r *http.Request) {
	ev, ok := decode(w, r)
	if !ok {
		return
	}
	allowed, reason := s.h.OnUp(ev)
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"allowed": allowed,
		"reason":  reason,
	})
}

func (s *Server) down(w http.ResponseWriter, r *http.Request) {
	ev, ok := decode(w, r)
	if !ok {
		return
	}
	s.h.OnDown(ev)
	w.WriteHeader(http.StatusNoContent)
}
