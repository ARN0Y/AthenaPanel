// athena-agent reports what a termination node is doing to the panel.
//
// Two properties are structural, not incidental:
//
//   - It is NEVER in the data path. pppd, xl2tpd, accel-ppp and the kernel's
//     WireGuard carry the traffic. Killing, restarting or upgrading this
//     process disconnects nobody, which is exactly what makes agent updates
//     cheap enough to do often.
//
//   - It reports ABSOLUTE counters and keeps no accounting state of its own.
//     Everything it knows is re-derived from the kernel on every tick, so it
//     may crash, be restarted, or lose its connection for an hour, and the
//     first report afterwards is still complete and correct.
package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"

	"github.com/ARN0Y/AthenaPanel/agent/internal/collect"
	pb "github.com/ARN0Y/AthenaPanel/agent/pb"
)

// Version is stamped at build time: -ldflags "-X main.Version=..."
var Version = "dev"

const protocolVersion = 1

type config struct {
	hub      string
	token    string
	wgIface  string
	interval time.Duration
	tls      bool
	insecure bool
}

func loadConfig() config {
	c := config{}
	flag.StringVar(&c.hub, "hub", env("ATHENA_HUB", "127.0.0.1:50051"), "hub address host:port")
	flag.StringVar(&c.token, "token", env("ATHENA_TOKEN", ""), "this node's token")
	flag.StringVar(&c.wgIface, "wg-iface", env("ATHENA_WG_IFACE", "wg-panel"), "WireGuard interface ('' to skip)")
	flag.DurationVar(&c.interval, "interval", 15*time.Second, "report interval")
	flag.BoolVar(&c.tls, "tls", env("ATHENA_TLS", "") == "1", "dial the hub over TLS")
	flag.BoolVar(&c.insecure, "tls-skip-verify", false, "do not verify the hub certificate (testing only)")
	showVersion := flag.Bool("version", false, "print version and exit")
	flag.Parse()
	if *showVersion {
		fmt.Println(Version)
		os.Exit(0)
	}
	return c
}

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func main() {
	log.SetFlags(log.LstdFlags | log.LUTC)
	cfg := loadConfig()
	if cfg.token == "" {
		log.Fatal("no token: set ATHENA_TOKEN or pass -token")
	}
	log.Printf("athena-agent %s starting; hub=%s wg=%q interval=%s tls=%v",
		Version, cfg.hub, cfg.wgIface, cfg.interval, cfg.tls)

	ctx, cancel := context.WithCancel(context.Background())
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		s := <-sig
		log.Printf("got %s, shutting down", s)
		cancel()
	}()

	// Reconnect forever with capped backoff. A node that cannot reach the hub
	// must keep serving users and keep trying quietly, not exit and leave
	// systemd to restart-loop it.
	backoff := time.Second
	for ctx.Err() == nil {
		start := time.Now()
		err := runSession(ctx, cfg)
		if ctx.Err() != nil {
			break
		}
		if err != nil {
			log.Printf("session ended: %v", err)
		}
		// A stream that survived a while was healthy; reset the backoff so a
		// brief network blip does not inherit an hour-old penalty.
		if time.Since(start) > 2*time.Minute {
			backoff = time.Second
		}
		log.Printf("reconnecting in %s", backoff)
		select {
		case <-ctx.Done():
		case <-time.After(backoff):
		}
		if backoff < 30*time.Second {
			backoff *= 2
		}
	}
	log.Print("stopped")
}

func dial(ctx context.Context, cfg config) (*grpc.ClientConn, error) {
	var creds grpc.DialOption
	if cfg.tls {
		creds = grpc.WithTransportCredentials(
			credentials.NewTLS(tlsConfig(cfg.insecure)),
		)
	} else {
		creds = grpc.WithTransportCredentials(insecure.NewCredentials())
	}
	dctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	return grpc.DialContext(dctx, cfg.hub, creds, grpc.WithBlock())
}

// runSession opens one stream and reports on it until it breaks.
func runSession(ctx context.Context, cfg config) error {
	conn, err := dial(ctx, cfg)
	if err != nil {
		return fmt.Errorf("dial %s: %w", cfg.hub, err)
	}
	defer conn.Close()

	client := pb.NewNodeHubClient(conn)
	stream, err := client.Connect(ctx)
	if err != nil {
		return fmt.Errorf("open stream: %w", err)
	}

	if err := stream.Send(&pb.AgentMessage{
		Payload: &pb.AgentMessage_Hello{Hello: &pb.Hello{
			Token:           cfg.token,
			AgentVersion:    Version,
			ProtocolVersion: protocolVersion,
			Hostname:        collect.Hostname(),
			Os:              collect.OSName(),
			Kernel:          collect.Kernel(),
		}},
	}); err != nil {
		return fmt.Errorf("send hello: %w", err)
	}

	// The hub replies with Welcome, or simply closes the stream when it does
	// not like us. Treat a closed stream here as "rejected" and back off.
	first, err := stream.Recv()
	if err != nil {
		return fmt.Errorf("no welcome (rejected or hub down): %w", err)
	}
	w := first.GetWelcome()
	if w == nil {
		return fmt.Errorf("expected welcome, got %T", first.Payload)
	}
	interval := cfg.interval
	if w.ReportIntervalSeconds > 0 {
		interval = time.Duration(w.ReportIntervalSeconds) * time.Second
	}
	log.Printf("connected as node %d (%s); reporting every %s",
		w.NodeId, w.NodeName, interval)

	// Drain acks so flow control never stalls; a read error is how we learn
	// the stream died.
	recvErr := make(chan error, 1)
	go func() {
		for {
			if _, err := stream.Recv(); err != nil {
				recvErr <- err
				return
			}
		}
	}()

	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	if err := sendReport(stream, cfg); err != nil {
		return err
	}
	for {
		select {
		case <-ctx.Done():
			_ = stream.CloseSend()
			return nil
		case err := <-recvErr:
			if err == io.EOF {
				return fmt.Errorf("hub closed the stream")
			}
			return fmt.Errorf("recv: %w", err)
		case <-ticker.C:
			if err := sendReport(stream, cfg); err != nil {
				return err
			}
		}
	}
}

func sendReport(stream pb.NodeHub_ConnectClient, cfg config) error {
	host := collect.Health(cfg.wgIface)
	rep := &pb.Report{
		SentAtUnixMs: time.Now().UnixMilli(),
		Host: &pb.Host{
			UptimeSeconds:     host.UptimeSeconds,
			Load1:             host.Load1,
			MemTotalBytes:     host.MemTotalBytes,
			MemAvailableBytes: host.MemAvailableBytes,
			Xl2TpdOk:          host.Xl2tpdOK,
			IpsecOk:           host.IpsecOK,
			AccelPppOk:        host.AccelPppOK,
			WireguardOk:       host.WireguardOK,
		},
	}

	// Only include the session list when the scan actually worked. Sending an
	// empty list after a failed read would tell the hub every user vanished.
	if ppp, ok := collect.PppInterfaces(); ok {
		for _, p := range ppp {
			rep.Ppp = append(rep.Ppp, &pb.PppSession{
				Ifname:  p.Ifname,
				RxBytes: p.RxBytes,
				TxBytes: p.TxBytes,
				PeerIp:  p.PeerIP,
				Pid:     p.Pid,
			})
		}
	} else {
		log.Print("ppp scan failed; omitting the session list from this report")
	}

	if cfg.wgIface != "" {
		if peers, ok := collect.WgPeers(cfg.wgIface); ok {
			for _, p := range peers {
				rep.Wg = append(rep.Wg, &pb.WgPeer{
					PublicKey:         p.PublicKey,
					RxBytes:           p.RxBytes,
					TxBytes:           p.TxBytes,
					LastHandshakeUnix: p.LastHandshake,
					Address:           p.Address,
				})
			}
		}
	}

	return stream.Send(&pb.AgentMessage{
		Payload: &pb.AgentMessage_Report{Report: rep},
	})
}
