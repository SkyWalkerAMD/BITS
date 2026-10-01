package bits

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"time"
)

type Client struct {
	URL, Token, Node string
	HTTP             *http.Client
}

func NewClient(origin, token, node, ca string) (*Client, error) {
	if err := ValidateURL(origin); err != nil {
		return nil, err
	}
	if !digestRE.MatchString(token) {
		return nil, errors.New("invalid credential")
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM([]byte(ca)) {
		return nil, errors.New("invalid trusted center certificate")
	}
	transport := &http.Transport{TLSClientConfig: &tls.Config{RootCAs: pool, MinVersion: tls.VersionTLS12},
		MaxIdleConnsPerHost: 4, ResponseHeaderTimeout: 15 * time.Second, IdleConnTimeout: 30 * time.Second}
	return &Client{URL: origin, Token: token, Node: node, HTTP: &http.Client{Transport: transport, Timeout: 2 * time.Minute,
		CheckRedirect: func(r *http.Request, via []*http.Request) error { return errors.New("redirects are not allowed") }}}, nil
}
func (c *Client) Request(ctx context.Context, method, path, attempt string, body io.Reader, length int64) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, method, c.URL+path, body)
	if err != nil {
		return nil, err
	}
	req.ContentLength = length
	req.Header.Set("Authorization", "Bearer "+c.Token)
	req.Header.Set("X-BITS-Request", "1")
	req.Header.Set("Content-Type", "application/json")
	if c.Node != "" {
		req.Header.Set("X-BITS-Node", c.Node)
		req.Header.Set("X-BITS-Version", Version)
	}
	if attempt != "" {
		req.Header.Set("X-BITS-Attempt", attempt)
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		defer resp.Body.Close()
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return nil, fmt.Errorf("center HTTP %d: %s", resp.StatusCode, string(b))
	}
	return resp, nil
}
func (c *Client) JSON(ctx context.Context, method, path, attempt string, in, out any) error {
	raw := []byte("{}")
	var err error
	if in != nil {
		raw, err = json.Marshal(in)
		if err != nil {
			return err
		}
	}
	resp, err := c.Request(ctx, method, path, attempt, bytes.NewReader(raw), int64(len(raw)))
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	d := json.NewDecoder(io.LimitReader(resp.Body, 4<<20))
	if out == nil {
		_, err = io.Copy(io.Discard, io.LimitReader(resp.Body, 4<<20))
		return err
	}
	return d.Decode(out)
}
func (c *Client) Download(ctx context.Context, path, attempt string, target io.Writer) error {
	resp, err := c.Request(ctx, "GET", path, attempt, nil, 0)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	n, err := io.Copy(target, io.LimitReader(resp.Body, (16<<30)+1))
	if err == nil && n > 16<<30 {
		err = errors.New("download exceeds file limit")
	}
	return err
}
func (c *Client) Readback(ctx context.Context, path, attempt string, size int64, target io.Writer) error {
	// Hash every byte in bounded HTTPS requests. A slow multi-day evidence file
	// must not require completing the whole download inside one request timeout.
	if size == 0 {
		return c.Download(ctx, path, attempt, target)
	}
	for offset := int64(0); offset < size; {
		length := size - offset
		if length > 4<<20 {
			length = 4 << 20
		}
		req, err := http.NewRequestWithContext(ctx, "GET", c.URL+path, nil)
		if err != nil {
			return err
		}
		req.Header.Set("Authorization", "Bearer "+c.Token)
		req.Header.Set("X-BITS-Node", c.Node)
		req.Header.Set("X-BITS-Attempt", attempt)
		req.Header.Set("Range", "bytes="+strconv.FormatInt(offset, 10)+"-"+strconv.FormatInt(offset+length-1, 10))
		resp, err := c.HTTP.Do(req)
		if err != nil {
			return err
		}
		expected := "bytes " + strconv.FormatInt(offset, 10) + "-" + strconv.FormatInt(offset+length-1, 10) + "/" + strconv.FormatInt(size, 10)
		if resp.StatusCode != 206 || resp.Header.Get("Content-Range") != expected {
			resp.Body.Close()
			return errors.New("invalid range readback response")
		}
		n, err := io.Copy(target, io.LimitReader(resp.Body, length+1))
		resp.Body.Close()
		if err != nil {
			return err
		}
		if n != length {
			return errors.New("incomplete range readback")
		}
		offset += length
	}
	return nil
}
func LoadNodeConfig(path string) (NodeConfig, error) {
	var cfg NodeConfig
	if err := ReadCredential(path, &cfg); err != nil {
		return cfg, err
	}
	host, err := os.Hostname()
	if err != nil {
		return cfg, err
	}
	if cfg.Node != host || !ValidName(cfg.Serial) {
		return cfg, errors.New("node hostname/serial differs from explicit enrollment")
	}
	_, err = NewClient(cfg.URL, cfg.Token, cfg.Node, cfg.CA)
	return cfg, err
}
