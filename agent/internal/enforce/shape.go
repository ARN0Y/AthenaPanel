package enforce

import (
	"fmt"
	"os/exec"
	"strconv"
)

// ApplyShaping puts a user's speed limit on their ppp interface.
//
// Without this a customer moved to a node keeps paying for their tier and
// receives an unmetered line, because the rate travels all the way here in
// UserSync and then nothing acts on it. The master has shaped its own sessions
// from the start; a node simply never learned how.
//
// Same mechanism as the master, deliberately: htb on egress for download,
// ingress policing for upload. A node that shaped differently would mean two
// customers on the same package getting measurably different service depending
// on which server they landed on.
//
// The rate is taken from the account list the agent already holds rather than
// fetched per session. The master asks its API over loopback at this point; here
// that would be a WAN round trip inside the moment a customer is connecting,
// which is the one place latency is least affordable.
func ApplyShaping(ifname string, downKbps, upKbps uint32) error {
	if ifname == "" {
		return nil
	}
	var firstErr error
	note := func(err error) {
		if err != nil && firstErr == nil {
			firstErr = err
		}
	}

	// Download: shape what leaves the interface toward the client.
	//
	// The fq_codel leaf matters. Given no leaf, htb queues into a pfifo sized
	// by the device txqueuelen, and pppd brings ppp interfaces up with a
	// txqueuelen of 3. Measured on a shaped 20Mbit line at 80ms RTT, forwarded
	// (which is the real case — forwarded traffic gets no TCP Small Queues and
	// no pacing to keep the queue shallow): 370 packets dropped per 20MB
	// without the leaf, 0 with it, for ~2% more throughput. The throughput is
	// the small part. The drops are retransmits and latency spikes, and
	// fq_codel additionally keeps one bulk flow from starving a call sharing
	// the same shaped line.
	if downKbps > 0 {
		rate := strconv.FormatUint(uint64(downKbps), 10) + "kbit"
		_ = run("tc", "qdisc", "del", "dev", ifname, "root")
		note(runErr("tc", "qdisc", "add", "dev", ifname, "root", "handle", "1:", "htb", "default", "10"))
		note(runErr("tc", "class", "add", "dev", ifname, "parent", "1:", "classid", "1:10",
			"htb", "rate", rate, "ceil", rate, "burst", "15k"))
		note(runErr("tc", "qdisc", "add", "dev", ifname, "parent", "1:10", "handle", "10:", "fq_codel"))
	}

	// Upload: police what arrives from the client. There is nothing to queue on
	// ingress, so excess is dropped rather than delayed — which is what a
	// policer is for and why the two directions are not symmetrical.
	if upKbps > 0 {
		rate := strconv.FormatUint(uint64(upKbps), 10) + "kbit"
		_ = run("tc", "qdisc", "del", "dev", ifname, "ingress")
		note(runErr("tc", "qdisc", "add", "dev", ifname, "handle", "ffff:", "ingress"))
		note(runErr("tc", "filter", "add", "dev", ifname, "parent", "ffff:", "protocol", "ip",
			"prio", "1", "u32", "match", "u32", "0", "0",
			"police", "rate", rate, "burst", rate, "mtu", "1500", "drop", "flowid", ":1"))
	}
	return firstErr
}

func run(name string, args ...string) error {
	return exec.Command(name, args...).Run()
}

func runErr(name string, args ...string) error {
	out, err := exec.Command(name, args...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s %v: %w (%s)", name, args, err, out)
	}
	return nil
}
