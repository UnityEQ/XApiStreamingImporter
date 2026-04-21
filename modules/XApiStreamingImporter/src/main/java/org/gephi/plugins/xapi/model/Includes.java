package org.gephi.plugins.xapi.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.Collections;
import java.util.List;

@JsonIgnoreProperties(ignoreUnknown = true)
public class Includes {

    @JsonProperty("users")
    public List<User> users;

    @JsonProperty("tweets")
    public List<Tweet> tweets;

    public List<User> safeUsers() {
        return users == null ? Collections.emptyList() : users;
    }

    public List<Tweet> safeTweets() {
        return tweets == null ? Collections.emptyList() : tweets;
    }
}
